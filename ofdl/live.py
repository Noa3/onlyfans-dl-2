"""Record clear, muxed HLS segments without forwarding account credentials.

Inputs are explicit HTTPS OnlyFans/CDN playlist URLs. No account join/purchase
endpoints, key fetching, or decryption are involved. MPEG-TS and single-init
fragmented MP4 are saved in their native containers.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from uuid import uuid4

import requests

from .common import AppError, Cancelled, Control, EventSink, no_events
from .downloads import safe_component, safe_path, validate_media_url
from .http import proxy_mapping

MAX_PLAYLIST_BYTES = 2 * 1024 * 1024
MAX_SEGMENT_BYTES = 64 * 1024 * 1024
STALL_SECONDS = 120


@dataclass(repr=False)
class LiveOptions:
    url: str
    output_dir: Path
    label: str = 'live'
    max_seconds: float = 0
    proxy: str = ''
    user_agent: str = 'Mozilla/5.0'

    def validate(self):
        validate_media_url(self.url)
        self.output_dir = Path(self.output_dir).expanduser().resolve()
        self.label = safe_component(self.label or 'live')[:80]
        if not 0 <= self.max_seconds <= 7 * 24 * 3600:
            raise AppError('Recording duration must be between 0 and 7 days (0 means until stopped).')
        proxy_mapping(self.proxy)
        return self


@dataclass
class RecordingResult:
    path: Path | None
    segments: int
    media_seconds: float
    bytes_written: int
    reason: str


class HLSReader:
    def __init__(self, options: LiveOptions, control: Control, session=None):
        self.control = control
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.clear()
        self.session.cookies.clear()
        self.session.proxies = proxy_mapping(options.proxy) or {}
        self.session.headers.update({'User-Agent': options.user_agent,
                                     'Referer': 'https://onlyfans.com/',
                                     'Accept-Encoding': 'identity'})

    def close(self):
        self.session.close()

    def fetch(self, url: str, maximum: int) -> tuple[bytes, str]:
        for _ in range(6):
            self.control.checkpoint()
            validate_media_url(url)
            response = None
            try:
                response = self.session.get(url, timeout=(10, 20), stream=True,
                                            allow_redirects=False, verify=True)
                if response.status_code in {301,302,303,307,308}:
                    target = response.headers.get('Location')
                    if not target:
                        raise AppError('The stream server returned a redirect without a destination.')
                    url = urljoin(url, target)
                    continue
                if response.status_code != 200:
                    raise AppError(f'Live recording stopped: stream request returned HTTP {response.status_code}. Refresh the accessible playlist URL on the website.')
                data = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    self.control.checkpoint()
                    data.extend(chunk)
                    if len(data) > maximum:
                        raise AppError('Live stream response exceeded the size limit.')
                if not data:
                    raise AppError('The live stream server returned an empty response.')
                return bytes(data), url
            except requests.RequestException:
                raise AppError('Live stream connection failed. Completed segments are kept; refresh the playlist URL before starting a new recording.') from None
            finally:
                if response is not None:
                    response.close()
        raise AppError('The live stream server returned too many redirects.')

    def playlist(self, url: str):
        try:
            import m3u8
        except ImportError:
            raise AppError('Live recording requires m3u8. Install requirements.txt and restart the app.') from None
        body, final_url = self.fetch(url, MAX_PLAYLIST_BYTES)
        try:
            text = body.decode('utf-8-sig')
            if not text.lstrip().startswith('#EXTM3U'):
                raise ValueError
            playlist = m3u8.loads(text, uri=final_url)
            keys = list(playlist.keys or []) + list(playlist.session_keys or [])
            if any(key and key.method != 'NONE' for key in keys):
                raise AppError('Encrypted/DRM HLS is not supported; no keys were requested.')
            return playlist, final_url
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise AppError('The URL did not return a supported HLS playlist.') from None

    def media_playlist(self, url: str):
        for _ in range(5):
            playlist, url = self.playlist(url)
            if not playlist.is_variant:
                return playlist, url
            external_audio = {item.group_id for item in playlist.media
                              if item.type == 'AUDIO' and item.uri}
            candidates = [item for item in playlist.playlists
                          if item.stream_info.audio not in external_audio]
            if not candidates:
                raise AppError('This HLS master uses separate audio playlists. Supply a muxed audio/video playlist instead.')
            selected = max(candidates, key=lambda item: item.stream_info.bandwidth or 0)
            url = selected.absolute_uri
            validate_media_url(url)
        raise AppError('Too many nested HLS master playlists.')


def _check_segment(data: bytes, fragmented: bool, *, init: bool = False) -> None:
    if fragmented:
        allowed = {b'ftyp', b'moov'} if init else {b'styp', b'moof', b'sidx', b'emsg'}
        valid = len(data) >= 8 and data[4:8] in allowed
    else:
        valid = len(data) >= 188 and len(data) % 188 == 0 and all(
            data[offset] == 0x47 for offset in range(0, len(data), 188))
    if not valid:
        raise AppError('The stream returned unsupported media or non-media content; it was not appended to the recording.')


def record_live(options: LiveOptions, control: Control, emit: EventSink = no_events,
                *, session=None) -> RecordingResult:
    options.validate()
    reader = HLSReader(options, control, session)
    stream = None
    part = final = None
    segments = size = 0
    media_seconds = 0.0
    last_sequence = None
    init_url = None
    started = last_new = time.monotonic()
    reason = 'ended'
    try:
        emit('live_status', 'Reading accessible HLS playlist…')
        playlist, playlist_url = reader.media_playlist(options.url)
        while True:
            control.checkpoint()
            if options.max_seconds and time.monotonic() - started >= options.max_seconds:
                reason = 'duration limit'
                break
            if playlist.is_variant:
                raise AppError('The media playlist changed into a master; start a new recording.')
            indexed = list(enumerate(playlist.segments, start=playlist.media_sequence or 0))
            if last_sequence is None and not playlist.is_endlist:
                indexed = indexed[-3:]  # Join near the live edge, not at the beginning of a long DVR window.
            if indexed and last_sequence is not None and indexed[-1][0] < last_sequence:
                raise AppError('The live stream sequence restarted; completed segments are kept in the partial file.')
            for sequence, segment in indexed:
                if last_sequence is not None and sequence <= last_sequence:
                    continue
                control.checkpoint()
                if options.max_seconds and time.monotonic() - started >= options.max_seconds:
                    reason = 'duration limit'
                    break
                if last_sequence is not None and sequence != last_sequence + 1:
                    raise AppError('Live segments were missed; recording stopped rather than silently joining a gap.')
                if segment.discontinuity and segments:
                    raise AppError('Stream discontinuity detected; start a new recording. Earlier segments are kept.')
                if segment.byterange or getattr(segment, 'gap_tag', False):
                    raise AppError('Byte-range or missing HLS segments are not supported by this recorder.')
                section = segment.init_section
                new_init = section.absolute_uri if section else None
                if section and section.byterange:
                    raise AppError('Byte-range HLS initialization sections are not supported.')
                if segments and new_init != init_url:
                    raise AppError('Stream initialization changed; start a new recording. Earlier segments are kept.')
                initialization = b''
                if not segments and new_init:
                    initialization, _ = reader.fetch(new_init, MAX_SEGMENT_BYTES)
                    _check_segment(initialization, True, init=True)
                data, _ = reader.fetch(segment.absolute_uri, MAX_SEGMENT_BYTES)
                _check_segment(data, bool(new_init))
                if stream is None:
                    options.output_dir.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                    suffix = '.mp4' if new_init else '.ts'
                    name = f'{options.label}_{stamp}_{uuid4().hex[:12]}{suffix}'
                    final = safe_path(options.output_dir, name)
                    part = safe_path(options.output_dir, name + '.part')
                    stream = part.open('xb')
                    stream.write(initialization)
                    size += len(initialization)
                    init_url = new_init
                stream.write(data)
                stream.flush()
                segments += 1
                size += len(data)
                media_seconds += float(segment.duration or 0)
                last_sequence = sequence
                last_new = time.monotonic()
                emit('live_progress', {'segments':segments, 'bytes':size, 'media_seconds':media_seconds,
                                       'elapsed':last_new-started, 'path':str(part)})
            if reason == 'duration limit' or playlist.is_endlist:
                break
            if time.monotonic() - last_new >= STALL_SECONDS:
                raise AppError('No new live segments arrived for two minutes. Completed segments are kept in the partial file.')
            delay = max(1.0, min(10.0, float(playlist.target_duration or 4) / 2))
            if options.max_seconds:
                delay = min(delay, max(0, options.max_seconds - (time.monotonic()-started)))
            control.wait(delay)
            playlist, playlist_url = reader.playlist(playlist_url)
    except Cancelled:
        reason = 'stopped'
    except OSError:
        raise AppError('Recording file could not be written. Check free space and folder permissions; any partial recording is kept.') from None
    finally:
        reader.close()
        if stream is not None:
            stream.close()
    if part is not None and segments:
        # Rename only on a normal end, explicit stop or duration limit. Errors
        # leave .part intact and cannot advertise a complete recording.
        try:
            part.rename(final)
        except OSError:
            raise AppError('Recording stopped but the final rename failed. The .part file is kept.') from None
    return RecordingResult(final, segments, media_seconds, size, reason)
