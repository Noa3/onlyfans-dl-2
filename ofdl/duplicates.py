"""Content-hash duplicate report for the local media library.

The download manifest already stops *repeat downloads* by media identity. This
module answers the other question: are the same bytes stored twice on disk (old
folder layouts, re-downloads under a different name, copied profile trees)?

Only files inside the chosen library folder are read. Nothing is deleted without
an explicit confirmation. Both keeper and extra are re-hashed before deletion;
a missing, changed or unsafe keeper stops deletion for the group.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .common import AppError, Cancelled, Control, EventSink, no_events
from .downloads import MEDIA_EXTENSIONS
from .settings import config_dir

HASH_CHUNK = 1024 * 1024


@dataclass
class DuplicateGroup:
    digest: str
    size: int
    files: list[Path]
    keep: Path

    @property
    def extras(self) -> list[Path]:
        return [path for path in self.files if path != self.keep]

    @property
    def reclaimable(self) -> int:
        return self.size * len(self.extras)


class HashCache:
    """Reuse hashes for unchanged files, keyed by path + size + mtime."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else config_dir() / 'file_hashes.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.execute('CREATE TABLE IF NOT EXISTS hashes '
                        '(path TEXT PRIMARY KEY, size INTEGER NOT NULL, '
                        'mtime_ns INTEGER NOT NULL, digest TEXT NOT NULL)')
        self.db.commit()

    def get(self, path: Path, size: int, mtime_ns: int) -> str | None:
        try:
            row = self.db.execute('SELECT digest FROM hashes WHERE path=? AND size=? AND mtime_ns=?',
                                  (str(path), size, mtime_ns)).fetchone()
        except sqlite3.DatabaseError:
            return None
        return str(row[0]) if row else None

    def put(self, path: Path, size: int, mtime_ns: int, digest: str) -> None:
        try:
            self.db.execute('INSERT OR REPLACE INTO hashes VALUES (?,?,?,?)',
                            (str(path), size, mtime_ns, digest))
        except sqlite3.DatabaseError:
            pass

    def flush(self) -> None:
        try:
            self.db.commit()
        except sqlite3.DatabaseError:
            pass

    def close(self) -> None:
        self.flush()
        self.db.close()


def hash_file(path: Path, control: Control) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, 'rb') as stream:
            while True:
                control.checkpoint()
                chunk = stream.read(HASH_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        raise AppError(f'Could not read {path.name} while checking duplicates.') from None
    return digest.hexdigest()


def iter_media_files(root: Path, control: Control) -> Iterator[Path]:
    """Yield regular media files below ``root`` without following links."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise AppError('Choose an existing library folder that is not a symbolic link.')
    stack = [root]
    while stack:
        control.checkpoint()
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if not entry.name.startswith('.'):
                        stack.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                name = entry.name
                if name.startswith('.') or name.lower().endswith('.part'):
                    continue
                if Path(name).suffix.lower() not in MEDIA_EXTENSIONS:
                    continue
                yield Path(entry.path)
            except OSError:
                continue


def _recorded_paths(root: Path) -> set[str]:
    """Absolute paths the download manifest already knows, used to pick a keeper."""
    database = root / '.ofdl.sqlite3'
    if not database.is_file():
        return set()
    try:
        db = sqlite3.connect(f'file:{database}?mode=ro', uri=True, timeout=2)
        try:
            rows = db.execute('SELECT path FROM media').fetchall()
        finally:
            db.close()
    except sqlite3.DatabaseError:
        return set()
    recorded: set[str] = set()
    for (relative,) in rows:
        try:
            candidate = (root / str(relative)).resolve()
            if candidate.is_file():
                recorded.add(str(candidate))
        except (OSError, ValueError):
            continue
    return recorded


def _inside(root: Path, path: Path) -> Path:
    root = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise AppError('Refused a duplicate path outside the chosen library folder.') from None
    return resolved


def find_duplicates(root: Path, control: Control, *, cache: HashCache | None = None,
                    emit: EventSink = no_events) -> list[DuplicateGroup]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise AppError('Choose an existing library folder that is not a symbolic link.')
    owns_cache = cache is None
    cache = cache or HashCache()
    try:
        by_size: dict[int, list[Path]] = {}
        total = 0
        for path in iter_media_files(root, control):
            total += 1
            try:
                info = path.stat()
            except OSError:
                continue
            if info.st_size <= 0:
                continue
            by_size.setdefault(info.st_size, []).append(path)
        candidates = [path for files in by_size.values() if len(files) > 1 for path in files]
        emit('log', f'Scanned {total} media file(s); {len(candidates)} share a size and need hashing.')
        by_content: dict[tuple[int, str], list[Path]] = {}
        done = 0
        for path in candidates:
            control.checkpoint()
            try:
                info = path.stat()
            except OSError:
                continue
            digest = cache.get(path, info.st_size, info.st_mtime_ns)
            if digest is None:
                digest = hash_file(path, control)
                cache.put(path, info.st_size, info.st_mtime_ns, digest)
            by_content.setdefault((info.st_size, digest), []).append(path)
            done += 1
            emit('dup_progress', {'done': done, 'total': len(candidates), 'file': path.name})
        cache.flush()
        recorded = _recorded_paths(root)
        groups: list[DuplicateGroup] = []
        for (size, digest), files in by_content.items():
            if len(files) < 2:
                continue
            keeper = _choose_keeper(root, files, recorded)
            groups.append(DuplicateGroup(digest=digest, size=size,
                                         files=sorted(files, key=lambda item: str(item).lower()),
                                         keep=keeper))
        groups.sort(key=lambda group: group.reclaimable, reverse=True)
        emit('log', f'Duplicate check finished: {len(groups)} group(s), '
                    f'{sum(len(g.extras) for g in groups)} extra copy/copies.')
        return groups
    finally:
        if owns_cache:
            cache.close()


def _choose_keeper(root: Path, files: list[Path], recorded: set[str]) -> Path:
    def score(path: Path):
        try:
            resolved = str(path.resolve())
            known = resolved in recorded
            relative = path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            known, relative = False, Path(str(path))
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        # Prefer the copy the manifest owns, then the shallowest/shortest path,
        # then the oldest file, then a stable name so repeated scans agree.
        return (not known, len(relative.parts), len(str(relative)), mtime, str(relative).lower())
    return sorted(files, key=score)[0]


def _verify_copy(path: Path, group: DuplicateGroup, root: Path, control: Control) -> os.stat_result:
    """Validate a live copy, including changes while hashing it."""
    _inside(root, path)
    # Reject links in ancestor directories too, including Windows junctions.
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise AppError('Copy uses a symbolic link or junction; scan again.')
    if not path.is_file():
        raise AppError('Copy is no longer a regular file; scan again.')
    before = path.stat()
    if before.st_size != group.size or hash_file(path, control) != group.digest:
        raise AppError('Copy size or content changed; scan again.')
    after = path.stat()
    if _file_stamp(before) != _file_stamp(after):
        raise AppError('Copy changed while being verified; scan again.')
    return after


def _file_stamp(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def delete_extras(group: DuplicateGroup, root: Path, control: Control,
                  emit: EventSink = no_events) -> tuple[int, int, list[str]]:
    """Delete non-kept copies after re-verifying size and content. Returns stats."""
    if group.keep not in group.files:
        raise AppError('The copy to keep is not part of this duplicate group.')
    if len(group.files) < 2:
        return 0, 0, []
    deleted = freed = 0
    errors: list[str] = []
    for path in group.extras:
        control.checkpoint()
        try:
            info = _verify_copy(path, group, root, control)
            # Verify the survivor after hashing the extra, immediately before
            # deletion. A missing/changed keeper stops this entire group.
            try:
                _verify_copy(group.keep, group, root, control)
            except Cancelled:
                raise
            except (OSError, AppError):
                errors.append('Keeper missing, changed or unsafe; remaining copies were kept. Scan again.')
                break
            if path.samefile(group.keep):
                errors.append(f'{path.name}: skipped, same file as keeper')
                continue
            if _file_stamp(info) != _file_stamp(path.stat()):
                errors.append(f'{path.name}: skipped, changed during verification')
                continue
            path.unlink()
            deleted += 1
            freed += info.st_size
            emit('log', f'Removed duplicate: {path}')
        except Cancelled:
            raise
        except (OSError, AppError) as exc:
            errors.append(f'{path.name}: {exc}')
    return deleted, freed, errors
