"""Discovery, safe file naming, deduplication and resumable streaming downloads."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import requests

from .api import ApiClient, ApiError, TIMEOUT, wait_retry
from .common import AppError, Cancelled, Control, EventSink, no_events
from .settings import atomic_json

CATEGORIES = ('posts','archived','stories','messages','purchased')
MEDIA_EXTENSIONS = {'.jpg','.jpeg','.png','.gif','.webp','.avif','.heic','.heif',
                    '.mp4','.m4v','.mov','.webm','.mkv','.mp3','.m4a','.aac','.ogg','.oga','.wav','.flac'}


def parse_profiles(text: str) -> list[str]:
    result: list[str] = []
    for token in re.split(r'[\s,;]+', text.strip()):
        if not token:
            continue
        if token.lower().startswith(('http://','https://')):
            try:
                url = urlsplit(token)
                if url.scheme != 'https' or url.hostname not in {'onlyfans.com','www.onlyfans.com'} or url.username or url.password or url.port not in (None,443):
                    raise ValueError
                token = unquote(url.path.strip('/'))
            except ValueError:
                raise AppError('Profile URLs must be HTTPS OnlyFans profile URLs.') from None
        token = token.lstrip('@').lower()
        if not re.fullmatch(r'[a-z0-9_][a-z0-9_.-]{0,79}',token) or token in {'.','..'}:
            raise AppError('Enter creator usernames or profile URLs, separated by commas, spaces, or new lines. Do not enter post URLs or folder paths.')
        if token not in result:
            result.append(token)
    return result


def safe_component(value: str) -> str:
    value = re.sub(r'[^A-Za-z0-9_.-]', '_', str(value)).strip(' .')[:100]
    value = value or 'unknown'
    reserved = {'CON','PRN','AUX','NUL'} | {f'COM{i}' for i in range(1,10)} | {f'LPT{i}' for i in range(1,10)}
    if value.split('.')[0].upper() in reserved:
        value = '_' + value
    return value


def safe_path(root: Path, relative: Path | str) -> Path:
    root = root.resolve()
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise AppError('A file path attempted to leave the chosen download folder.')
    target = root / relative
    if target.is_symlink():
        raise AppError('Refused to overwrite a symbolic link in the download folder.')
    try:
        target.resolve().relative_to(root)
    except (ValueError, OSError, RuntimeError):
        raise AppError('A file path or symbolic link leaves the chosen download folder.') from None
    return target


def validate_media_url(value: str) -> str:
    try:
        url = urlsplit(value)
        host = (url.hostname or '').lower()
        if (url.scheme != 'https' or url.username or url.password or url.port not in (None,443)
                or not (host == 'onlyfans.com' or host.endswith('.onlyfans.com'))
                or any(ord(char) < 32 for char in value)):
            raise ValueError
    except (ValueError, TypeError):
        raise AppError('Media URL/redirect rejected: only HTTPS OnlyFans media hosts on port 443 are allowed.') from None
    return value


def parse_date(value: Any) -> datetime | None:
    try:
        if value is None or value == '':
            return None
        if isinstance(value,(int,float)):
            return datetime.fromtimestamp(value,timezone.utc)
        date = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return (date if date.tzinfo else date.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (ValueError,TypeError,OverflowError,OSError):
        return None


@dataclass
class Options:
    output_dir: Path
    profiles: list[str]
    categories: tuple[str,...] = CATEGORIES
    photos: bool = True
    videos: bool = True
    audio: bool = True
    albums: bool = True
    subfolders: bool = True
    previews: bool = False
    since: datetime | None = None
    dry_run: bool = False
    # Explicit scope: an empty manual list must never mean "download everyone".
    all_subscriptions: bool = False
    skip_profiles: tuple[str, ...] = ()

    def validate(self) -> Options:
        self.output_dir = Path(self.output_dir).expanduser().resolve()
        self.skip_profiles = tuple(parse_profiles(' '.join(self.skip_profiles)))
        self.profiles = [] if self.all_subscriptions else parse_profiles(' '.join(self.profiles))
        if not self.all_subscriptions:
            if not self.profiles:
                raise AppError('Enter at least one creator in Only these creators, or choose All active subscriptions.')
            if not any(name not in self.skip_profiles for name in self.profiles):
                raise AppError('No creators remain after applying Skip creators. Remove a name from Skip creators or change your creator list.')
        if not self.categories or any(category not in CATEGORIES for category in self.categories):
            raise AppError('Select at least one valid content category.')
        if not any((self.photos,self.videos,self.audio)):
            raise AppError('Select photos, videos, or audio.')
        if self.since is not None:
            self.since = (self.since if self.since.tzinfo else self.since.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        return self


@dataclass(frozen=True, repr=False)
class MediaTask:
    owner_id: str
    profile: str
    media_id: str
    kind: str
    category: str
    date: str
    album: str
    url: str
    preview: bool = False

    @property
    def identity(self) -> str:
        return f'{self.owner_id}:{self.media_id}:{"preview" if self.preview else "full"}'

    def __repr__(self) -> str:
        return f'MediaTask(id={self.media_id!r}, category={self.category!r}, url=<redacted>)'

    def relative_path(self, options: Options) -> Path:
        suffix = Path(unquote(urlsplit(self.url).path)).suffix.lower()
        if suffix not in MEDIA_EXTENSIONS:
            suffix = {'photo':'.jpg','gif':'.gif','video':'.mp4','audio':'.mp3'}[self.kind]
        parts = [safe_component(self.profile)]
        if options.subfolders and self.category != 'posts':
            parts.append(self.category)
        parts.append({'photo':'photos','gif':'photos','video':'videos','audio':'audios'}[self.kind])
        if options.albums and self.album and self.kind in {'photo','gif'}:
            parts.append(safe_component(self.date + '_' + self.album))
        filename = safe_component(self.date + '_' + self.media_id + ('_preview' if self.preview else ''))
        return Path(*parts) / (filename + suffix)


def media_task(post: dict[str,Any], media: dict[str,Any], owner_id: str, profile: str,
               category: str, options: Options) -> tuple[MediaTask | None,str]:
    if not isinstance(media,dict):
        return None,'unavailable'
    kind = media.get('type')
    if kind not in {'photo','gif','video','audio'}:
        return None,'filtered'
    if ((kind in {'photo','gif'} and not options.photos) or (kind == 'video' and not options.videos)
            or (kind == 'audio' and not options.audio)):
        return None,'filtered'
    if post.get('canViewMedia') is False or media.get('canView') is False:
        return None,'unavailable'
    files = media.get('files') if isinstance(media.get('files'),dict) else {}
    if media.get('drm') or files.get('drm') or media.get('isDrm'):
        return None,'unavailable'
    date = parse_date(media.get('createdAt')) if category == 'stories' else None
    date = date or parse_date(post.get('postedAt')) or parse_date(post.get('createdAt'))
    if options.since is not None and (date is None or date < options.since):
        return None,'filtered'
    day = date.strftime('%Y-%m-%d') if date else '1970-01-01'
    full = files.get('full') if isinstance(files.get('full'),dict) else {}
    source = media.get('source') if isinstance(media.get('source'),dict) else {}
    url = full.get('url') or source.get('source')
    preview = False
    if not url and options.previews:
        small = files.get('preview') if isinstance(files.get('preview'),dict) else {}
        url = small.get('url')
        preview = bool(url)
    if not isinstance(url,str) or not url:
        return None,'unavailable'
    if Path(urlsplit(url).path).suffix.lower() in {'.mpd','.m3u8'}:
        return None,'unavailable'
    validate_media_url(url)
    media_id = str(media.get('id',''))
    if not re.fullmatch(r'[0-9]{1,24}',media_id):
        return None,'unavailable'
    items = post.get('media') if isinstance(post.get('media'),list) else []
    album = str(post.get('id','')) if len(items) > 1 else ''
    return MediaTask(str(owner_id),profile,media_id,kind,category,day,album,url,preview),''


class FolderLock:
    """OS file lock, released even after a process crash; no stale PID-file guessing."""
    def __init__(self, root: Path):
        self.root = Path(root)
        self.stream = None

    def __enter__(self):
        self.root.mkdir(parents=True,exist_ok=True)
        path = safe_path(self.root,'.ofdl.lock')
        self.stream = path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if path.stat().st_size == 0:
                    self.stream.write(b'0')
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            raise AppError('Another download run is using this output folder. Stop it or choose a different folder.') from None
        return self

    def __exit__(self,*args):
        if self.stream:
            try:
                if os.name == 'nt':
                    import msvcrt
                    self.stream.seek(0)
                    msvcrt.locking(self.stream.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(),fcntl.LOCK_UN)
            finally:
                self.stream.close()


class LegacyIndex:
    """Lazy index for files produced by the supplied original script.

    Exact current paths are checked first. A creator tree is only scanned after an exact
    miss, and each creator is scanned at most once per run. This keeps repeat runs with
    tens of thousands of correctly placed files cheap while still finding old album,
    category-subfolder, flat-folder, and /gifs/ layouts.
    """
    _name_re = re.compile(r'^.+_([0-9]{1,24})(?:_preview)?(\.[A-Za-z0-9]{2,8})$')
    _media_dirs = {'photos','videos','audios','gifs'}

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._scanned: set[str] = set()
        self._items: dict[tuple[str,str,str], list[tuple[int,Path]]] = {}

    def _profile_root(self, profile: str) -> Path | None:
        exact = self.root / safe_component(profile)
        if exact.is_dir() and not exact.is_symlink():
            return exact
        try:
            for child in self.root.iterdir():
                if child.is_dir() and not child.is_symlink() and child.name.casefold() == profile.casefold():
                    return child
        except OSError:
            return None
        return None

    @staticmethod
    def _kind(extension: str) -> str | None:
        extension = extension.lower()
        if extension == '.gif':
            return 'gif'
        if extension in {'.jpg','.jpeg','.png','.webp','.avif','.heic','.heif'}:
            return 'photo'
        if extension in {'.mp4','.m4v','.mov','.webm','.mkv'}:
            return 'video'
        if extension in {'.mp3','.m4a','.aac','.ogg','.oga','.wav','.flac'}:
            return 'audio'
        return None

    def _scan_profile(self, profile: str) -> None:
        key = profile.casefold()
        if key in self._scanned:
            return
        self._scanned.add(key)
        base = self._profile_root(profile)
        if base is None:
            return
        stack = [base]
        while stack:
            folder = stack.pop()
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    match = self._name_re.fullmatch(entry.name)
                    if not match:
                        continue
                    relative = Path(entry.path).relative_to(base)
                    folder_parts = {part.casefold() for part in relative.parts[:-1]}
                    if not (folder_parts & self._media_dirs):
                        continue
                    media_id, extension = match.groups()
                    kind = self._kind(extension)
                    if 'gifs' in folder_parts and kind in {'photo','gif'}:
                        kind = 'gif'
                    if kind is None:
                        continue
                    size = entry.stat(follow_symlinks=False).st_size
                    if size <= 0:
                        continue
                    item_key = (key, media_id, kind)
                    self._items.setdefault(item_key, []).append((size, Path(entry.path)))
                except (OSError, ValueError):
                    continue

    def find(self, task: MediaTask) -> Path | None:
        self._scan_profile(task.profile)
        candidates = self._items.get((task.profile.casefold(), task.media_id, task.kind), ())
        if not candidates:
            return None
        # Prefer the expected date naming when available, then the largest non-empty copy.
        expected_prefix = f'{task.date}_{task.media_id}'
        ordered = sorted(candidates, key=lambda item: (Path(item[1]).name.startswith(expected_prefix), item[0]), reverse=True)
        for _, path in ordered:
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_size > 0:
                    path.resolve().relative_to(self.root)
                    return path
            except (OSError, ValueError, RuntimeError):
                continue
        return None


class Manifest:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        database = safe_path(self.root,'.ofdl.sqlite3')
        for extra in ('.ofdl.sqlite3-wal','.ofdl.sqlite3-shm','.ofdl.sqlite3-journal'):
            safe_path(self.root,extra)
        self.db = sqlite3.connect(database,timeout=5)
        self._pending = 0
        try:
            self.db.execute('CREATE TABLE IF NOT EXISTS media (identity TEXT PRIMARY KEY, path TEXT NOT NULL, size INTEGER NOT NULL, completed_at TEXT NOT NULL)')
            self.db.commit()
        except Exception:
            self.db.close()
            raise

    def flush(self):
        if self._pending:
            self.db.commit()
            self._pending = 0

    def close(self):
        self.flush()
        self.db.close()

    def recorded(self, task: MediaTask) -> bool:
        return self.db.execute('SELECT 1 FROM media WHERE identity=?',(task.identity,)).fetchone() is not None

    def find(self, task: MediaTask) -> Path | None:
        row = self.db.execute('SELECT path,size FROM media WHERE identity=?',(task.identity,)).fetchone()
        if row:
            path = safe_path(self.root,row[0])
            if path.is_file() and path.stat().st_size == row[1] and row[1] > 0:
                return path
        return None

    def record(self, task: MediaTask, path: Path, *, durable: bool = True) -> None:
        relative = path.resolve().relative_to(self.root).as_posix()
        size = path.stat().st_size
        if size <= 0:
            raise AppError('Refused to mark an empty file as complete.')
        self.db.execute('INSERT OR REPLACE INTO media VALUES (?,?,?,?)',
                        (task.identity,relative,size,datetime.now(timezone.utc).isoformat()))
        if durable:
            self.db.commit()
            self._pending = 0
        else:
            self._pending += 1
            if self._pending >= 500:
                self.flush()


@dataclass
class RunSummary:
    planned: int = 0
    downloaded: int = 0
    existing: int = 0
    duplicates: int = 0
    filtered: int = 0
    unavailable: int = 0
    errors: int = 0
    transferred_bytes: int = 0

    def to_dict(self) -> dict[str,int]:
        return asdict(self)


class Downloader:
    def __init__(self, options: Options, user_agent: str, control: Control, manifest: Manifest,
                 *, session: requests.Session | None = None, retries: int = 3,
                 emit: EventSink = no_events):
        self.options = options
        self.root = Path(options.output_dir).resolve()
        self.control = control
        self.manifest = manifest
        self.user_agent = user_agent
        # Deliberately not the API session: it has no sess/x-bc/sign/user-id credentials.
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.clear()
        self.session.cookies.clear()
        self.retries = max(0,min(5,retries))
        self.emit = emit
        self.legacy_index = LegacyIndex(self.root)

    def close(self):
        self.session.close()

    def _get(self, url: str, headers: dict[str,str]) -> requests.Response:
        for _ in range(6):
            self.control.checkpoint()
            validate_media_url(url)
            self.session.cookies.clear()
            response = self.session.get(url,headers=headers,timeout=TIMEOUT,verify=True,
                                        stream=True,allow_redirects=False)
            if response.status_code not in {301,302,303,307,308}:
                return response
            location = response.headers.get('Location')
            response.close()
            if not location:
                raise AppError('The media server returned an empty redirect.')
            url = urljoin(url,location)
            validate_media_url(url)
        raise AppError('The media server returned too many redirects.')

    def download(self, task: MediaTask) -> tuple[str,int]:
        self.control.checkpoint()
        validate_media_url(task.url)
        relative = task.relative_path(self.options)
        target = safe_path(self.root,relative)
        part = safe_path(self.root,str(relative) + '.part')
        state_path = safe_path(self.root,str(relative) + '.part.json')
        found = self.manifest.find(task)
        if found:
            self.emit('log',f'Already downloaded: {found.relative_to(self.root)}')
            return 'existing',0
        # Only trust legacy files if a manifest entry does not already say their size changed.
        recorded = self.manifest.recorded(task)
        if target.is_file() and target.stat().st_size > 0 and not recorded:
            self.manifest.record(task,target,durable=False)
            self.emit('log',f'Existing file adopted: {relative}')
            return 'existing',0
        if not recorded:
            legacy = self.legacy_index.find(task)
            if legacy is not None and legacy != target:
                self.manifest.record(task,legacy,durable=False)
                self.emit('log',f'Existing legacy file adopted: {legacy.relative_to(self.root)}')
                return 'existing',0
        target.parent.mkdir(parents=True,exist_ok=True)
        bytes_received = 0
        restarted_416 = False
        for attempt in range(self.retries + 1):
            self.control.checkpoint()
            offset = 0
            validator = ''
            previous_total = 0
            if part.exists() and state_path.exists():
                try:
                    if state_path.stat().st_size > 8192:
                        raise ValueError
                    state = json.loads(state_path.read_text(encoding='utf-8'))
                    value = state.get('validator','')
                    if (state.get('identity') == task.identity and isinstance(value,str)
                            and value and not value.startswith('W/') and len(value) < 2048
                            and not any(ord(c) < 32 or ord(c) > 126 for c in value)):
                        offset = part.stat().st_size
                        validator = value
                        previous_total = int(state.get('total',0))
                except (OSError,ValueError,TypeError,AttributeError):
                    pass
            headers = {'User-Agent':self.user_agent,'Referer':'https://onlyfans.com/',
                       'Accept':'*/*','Accept-Encoding':'identity'}
            if offset > 0 and validator:
                headers.update({'Range':f'bytes={offset}-','If-Range':validator})
            else:
                offset = 0
            response = None
            retry_header = None
            try:
                response = self._get(task.url,headers)
                if response.status_code == 416 and offset and not restarted_416:
                    # A matching size is not proof of identity. Restart once, never promote blindly.
                    response.close()
                    response = None
                    part.unlink(missing_ok=True)
                    state_path.unlink(missing_ok=True)
                    restarted_416 = True
                    headers.pop('Range',None)
                    headers.pop('If-Range',None)
                    offset = 0
                    response = self._get(task.url,headers)
                status = response.status_code
                if status in {401,403,404}:
                    raise AppError(f'Media request returned HTTP {status}; access or the temporary media URL may have expired. Re-scan to obtain fresh URLs.')
                if status == 429 or 500 <= status <= 599:
                    retry_header = response.headers.get('Retry-After')
                    if attempt >= self.retries:
                        raise ApiError(status)
                    response.close()
                    response = None
                    wait_retry(self.control,attempt,retry_header,self.emit)
                    continue
                if status not in {200,206}:
                    raise AppError(f'Media request returned unexpected HTTP {status}.')
                content_type = response.headers.get('Content-Type','').split(';')[0].strip().lower()
                if content_type and not (content_type.startswith(('image/','video/','audio/')) or content_type in {'application/octet-stream','binary/octet-stream','application/mp4'}):
                    raise AppError('The media server returned non-media content (possibly a login page); it was not saved as a completed file.')
                encoding = response.headers.get('Content-Encoding','identity').lower()
                if encoding not in {'','identity'}:
                    raise AppError('The media server ignored identity encoding. Stopped to avoid corrupt byte-range/length checks.')
                length_text = response.headers.get('Content-Length')
                if length_text is not None and not re.fullmatch(r'[0-9]+',length_text):
                    raise AppError('The media server returned an invalid Content-Length.')
                length = int(length_text) if length_text is not None else None
                current_validator = response.headers.get('ETag','')
                if current_validator.startswith('W/') or not current_validator:
                    current_validator = response.headers.get('Last-Modified','')
                if any(ord(c) < 32 or ord(c) > 126 for c in current_validator) or len(current_validator) >= 2048:
                    current_validator = ''
                if status == 206:
                    match = re.fullmatch(r'bytes ([0-9]+)-([0-9]+)/([0-9]+)',response.headers.get('Content-Range',''))
                    if not offset or not match:
                        raise AppError('The media server returned an unexpected or malformed partial response.')
                    start,end,total = map(int,match.groups())
                    if (start != offset or end != total - 1 or end < start
                            or (length is not None and length != end-start+1)
                            or (previous_total > 0 and total != previous_total)
                            or (current_validator and current_validator != validator)):
                        raise AppError('The media server returned an inconsistent byte range or changed file. The partial file was not marked complete.')
                    expected = total
                    mode = 'ab'
                    effective_validator = current_validator or validator
                else:
                    offset = 0
                    expected = length
                    mode = 'wb'
                    effective_validator = current_validator
                if expected == 0:
                    raise AppError('The media server returned an empty file.')
                # These sidecars contain identity/length/validator only, never signed URLs or cookies.
                atomic_json(state_path,{'identity':task.identity,'validator':effective_validator,'total':expected or 0})
                done = offset
                transfer_start = time.monotonic()
                last_progress = 0.0
                first = True
                with part.open(mode) as stream:
                    for chunk in response.iter_content(256 * 1024):
                        self.control.checkpoint()
                        if not chunk:
                            continue
                        if first and offset == 0:
                            head = chunk.lstrip()[:80].lower()
                            if head.startswith((b'<!doctype html',b'<html',b'{"error"',b'{"message"')):
                                raise AppError('An HTML/JSON error page was returned instead of media.')
                        first = False
                        stream.write(chunk)
                        done += len(chunk)
                        bytes_received += len(chunk)
                        if expected is not None and done > expected:
                            raise AppError('The received file exceeds its declared length; it was not marked complete.')
                        now = time.monotonic()
                        if now - last_progress >= .15:
                            self.emit('progress',{'file':str(relative),'done':done,'total':expected or 0,
                                                  'speed':(done-offset)/max(.001,now-transfer_start)})
                            last_progress = now
                    stream.flush()
                    os.fsync(stream.fileno())
                if done <= 0 or (expected is not None and done != expected):
                    raise AppError('The transfer ended before the declared file length. Its partial data was retained for a later retry.')
                self.control.checkpoint()
                # Re-check containment immediately before the final replacement.
                safe_path(self.root,relative)
                os.replace(part,target)
                self.manifest.record(task,target)
                state_path.unlink(missing_ok=True)
                self.emit('progress',{'file':str(relative),'done':done,'total':done,
                                      'speed':(done-offset)/max(.001,time.monotonic()-transfer_start)})
                self.emit('log',f'Downloaded: {relative}')
                self.emit('media_complete',{'path':str(target),'relative':str(relative),'kind':task.kind,
                                            'profile':task.profile,'media_id':task.media_id,
                                            'size':target.stat().st_size})
                return 'downloaded',bytes_received
            except requests.exceptions.SSLError:
                raise AppError('TLS verification failed for the media server. Certificate verification remains enabled.') from None
            except requests.RequestException:
                if attempt >= self.retries:
                    raise AppError('The media connection failed or timed out. Partial data is kept; rerun to retry.') from None
                if response is not None:
                    response.close()
                    response = None
                wait_retry(self.control,attempt,retry_header,self.emit)
            finally:
                if response is not None:
                    response.close()
        raise AppError('The download retry limit was reached.')


def resolve_creator_profiles(client: ApiClient, options: Options, control: Control,
                             emit: EventSink = no_events) -> list[str]:
    """Resolve a complete, fresh scope before scanning any creator's media.

    The caller validates options/account access first. No Tk state is accessed here.
    A partial subscription response is an error, never an incomplete successful list.
    """
    control.checkpoint()
    if options.all_subscriptions:
        emit('phase', 'Loading all active subscriptions')
        names: list[str] = []
        seen: set[str] = set()
        for row in client.paginate('/subscriptions/subscribes', 'subscriptions'):
            control.checkpoint()
            username = row.get('username') if isinstance(row, dict) else None
            if not isinstance(username, str) or not username.strip():
                raise AppError('A subscription has no valid username; creator discovery stopped. Try again or use Only these creators.')
            parsed = parse_profiles(username)
            if len(parsed) != 1:
                raise AppError('A subscription has an invalid username; creator discovery stopped.')
            name = parsed[0]
            if name not in seen:
                names.append(name)
                seen.add(name)
        control.checkpoint()
        if not names:
            raise AppError('No active subscriptions were returned. For accessible purchases from other creators, choose Only these creators and enter their usernames.')
        source = 'active subscriptions'
    else:
        names = list(options.profiles)
        source = 'your creator list'
    excluded = set(options.skip_profiles)
    included = [name for name in names if name not in excluded]
    emit('log', f'Creator selection: {len(included)} included, {len(names) - len(included)} skipped from {source}.')
    if not included:
        raise AppError('No creators remain after applying Skip creators. Remove a name from Skip creators or change your creator list.')
    return included


def run_downloads(client: ApiClient, options: Options, control: Control,
                  emit: EventSink = no_events) -> RunSummary:
    options.validate()
    summary = RunSummary()
    queue: dict[str,MediaTask] = {}
    owners: dict[str,str] = {}
    emit('phase','Scanning accessible content')
    # Validate account access once before enumerating creators.
    client.user('me')
    profiles = resolve_creator_profiles(client, options, control, emit)
    emit('phase', 'Scanning accessible content')

    def accept(post: dict[str,Any], uid: str, name: str, category: str) -> None:
        if not isinstance(post,dict):
            summary.errors += 1
            return
        media_items = post.get('media',[])
        if not isinstance(media_items,list):
            return
        for media in media_items:
            control.checkpoint()
            try:
                task,reason = media_task(post,media,uid,name,category,options)
            except AppError as exc:
                summary.errors += 1
                emit('log',f'{name}/{category}: {exc}')
                continue
            if task:
                if task.identity in queue:
                    summary.duplicates += 1
                else:
                    queue[task.identity] = task
                    summary.planned += 1
            elif reason == 'filtered':
                summary.filtered += 1
            else:
                summary.unavailable += 1
        emit('stats',summary.to_dict())

    def enumerate_category(uid: str, name: str, category: str, endpoint: str) -> None:
        try:
            for post in client.paginate(endpoint,category,since=options.since):
                accept(post,uid,name,category)
        except ApiError as exc:
            if exc.status in {401,429}:
                raise
            summary.errors += 1
            emit('log',f'Incomplete scan for {name}/{category}: {exc}')
        except Cancelled:
            raise
        except AppError as exc:
            summary.errors += 1
            emit('log',f'Incomplete scan for {name}/{category}: {exc}')

    for name in profiles:
        control.checkpoint()
        emit('log',f'Looking up {name}.')
        try:
            user = client.user(name)
        except ApiError as exc:
            if exc.status in {401,429}:
                raise
            summary.errors += 1
            emit('log',f'Skipping {name}: {exc}')
            continue
        uid = str(user['id'])
        if not re.fullmatch(r'[0-9]{1,24}',uid):
            summary.errors += 1
            emit('log',f'Skipping {name}: invalid account ID in profile response.')
            continue
        if uid in owners:
            continue
        owners[uid] = name
        routes = {'posts':f'/users/{uid}/posts','archived':f'/users/{uid}/posts/archived',
                  'stories':f'/users/{uid}/stories','messages':f'/chats/{uid}/messages'}
        for category in options.categories:
            if category != 'purchased':
                enumerate_category(uid,name,category,routes[category])
    if 'purchased' in options.categories and owners:
        emit('log','Scanning purchases once for all selected profiles.')
        try:
            for post in client.paginate('/posts/paid/all','purchased'):
                author = post.get('fromUser') or post.get('author') or {}
                if not isinstance(author,dict):
                    continue
                uid = str(author.get('id',''))
                name = owners.get(uid)
                if name is None:
                    author_name = str(author.get('username','')).lower()
                    for owner_id, profile in owners.items():
                        if author_name == profile:
                            uid,name = owner_id,profile
                            break
                if name:
                    accept(post,uid,name,'purchased')
        except ApiError as exc:
            if exc.status in {401,429}:
                raise
            summary.errors += 1
            emit('log',f'Incomplete purchased-content scan: {exc}')
        except Cancelled:
            raise
        except AppError as exc:
            summary.errors += 1
            emit('log',f'Incomplete purchased-content scan: {exc}')
    emit('stats',summary.to_dict())
    if options.dry_run:
        emit('phase','Scan finished; no media downloaded')
        for task in queue.values():
            emit('log',f'Planned: {task.relative_path(options)}')
        return summary
    if not queue:
        emit('phase','No downloadable media found')
        return summary
    control.checkpoint()
    emit('phase','Downloading')
    with FolderLock(options.output_dir):
        manifest = Manifest(options.output_dir)
        worker = Downloader(options,client.auth.user_agent,control,manifest,emit=emit)
        try:
            for index,task in enumerate(queue.values(),start=1):
                control.checkpoint()
                try:
                    result,size = worker.download(task)
                    if result == 'downloaded':
                        summary.downloaded += 1
                        summary.transferred_bytes += size
                    else:
                        summary.existing += 1
                except Cancelled:
                    raise
                except ApiError as exc:
                    if exc.status == 429:
                        raise
                    summary.errors += 1
                    emit('log',f'Failed media {task.media_id}: {exc}')
                except (AppError,OSError) as exc:
                    summary.errors += 1
                    detail = str(exc) if isinstance(exc,AppError) else 'Local file operation failed; check disk space and permissions.'
                    emit('log',f'Failed media {task.media_id}: {detail}')
                emit('overall',{'done':index,'total':len(queue)})
                emit('stats',summary.to_dict())
        finally:
            worker.close()
            manifest.close()
    emit('phase','Finished with issues' if summary.errors else 'Finished')
    return summary
