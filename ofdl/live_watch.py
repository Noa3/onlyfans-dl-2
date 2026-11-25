"""Read-only live discovery and bounded, opt-in recording supervision.

Endpoint/hasStream observations: OF-Scraper-GUI live_stream_monitor (see
THIRD_PARTY_NOTICES.md). Platform response shapes remain unofficial.
"""
from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .api import ApiClient, ApiError, load_rules
from .common import AppError, Cancelled, Control, no_events
from .downloads import parse_profiles
from .http import playwright_proxy


@dataclass(frozen=True)
class LiveStream:
    creator: str
    stream_id: str

    @property
    def key(self):
        return f'{self.creator}:{self.stream_id}'


@dataclass
class WatchOptions:
    output_dir: Path
    include: str = ''
    exclude: str = ''
    interval: int = 60
    parallel: int = 2
    max_seconds: float = 0
    channel: str = 'chromium'
    proxy: str = field(default='', repr=False)

    def validate(self):
        if not 30 <= self.interval <= 3600:
            raise AppError('Live checks must be between 30 and 3600 seconds.')
        if not 1 <= self.parallel <= 4:
            raise AppError('Choose between 1 and 4 parallel live recordings.')
        if not 0 <= self.max_seconds <= 7 * 86400:
            raise AppError('Maximum recording duration must be between 0 and 7 days.')
        if self.channel not in {'chromium', 'chrome', 'msedge'}:
            raise AppError('Choose a supported browser in Session.')
        parse_profiles(self.include)
        parse_profiles(self.exclude)
        playwright_proxy(self.proxy)
        self.output_dir = Path(self.output_dir).expanduser().resolve()
        return self


def discover_streams(client, options: WatchOptions, emit=no_events) -> list[LiveStream]:
    """Restrict even explicit filters to current subscriptions; never subscribe."""
    include, exclude = set(parse_profiles(options.include)), set(parse_profiles(options.exclude))
    streams = {}
    for row in client.paginate('/subscriptions/subscribes', 'subscriptions'):
        client.control.checkpoint()
        names = parse_profiles(str(row.get('username') or ''))
        if len(names) != 1:
            raise AppError('A subscription has no valid creator name; live scan incomplete.')
        name = names[0]
        if name in exclude or (include and name not in include):
            continue
        if 'hasStream' not in row or not isinstance(row['hasStream'], bool):
            raise AppError('Subscription live-status field is missing or changed; live scan incomplete.')
        if not row['hasStream']:
            continue
        try:
            data = client.request(f'/users/{name}/streams/active', {'view':'full_with_check'})
        except ApiError as exc:
            if exc.status == 404:  # Ended between subscription page and detail request.
                continue
            raise
        if data is None:
            continue
        if not isinstance(data, dict):
            raise AppError('Unexpected active-stream response; live scan incomplete.')
        if data.get('isActive') is False or data.get('isFinished') is True:
            continue
        sid = str(data.get('id', ''))
        if data.get('isActive') is not True or not re.fullmatch(r'[0-9]{1,24}', sid):
            raise AppError('Active stream is missing its status or ID; live scan incomplete.')
        stream = LiveStream(name, sid)
        streams[stream.key] = stream
    return list(streams.values())


class LiveWatcher:
    """All scheduling state belongs to run(); UI sends commands through a queue."""
    def __init__(self, auth, source, options, control, emit=no_events, *, recorder=None,
                 discover=None, clock=time.monotonic):
        self.auth, self.source = auth, source
        self.options = options.validate()
        self.control, self.emit = control, emit
        if recorder is None:
            from .live_browser import record_browser
            recorder = record_browser
        self.recorder = recorder
        self.discover = discover or self._discover
        self.clock = clock
        self.commands = queue.Queue()
        self.completed = queue.Queue()
        self.jobs = {}
        self.job_controls = {}
        self.job_lock = threading.Lock()
        self.finished = set()
        self.failures = {}
        self.retry_at = {}
        self.rows = {}
        self.missing = {}

    def stop_selected(self, keys):
        keys = tuple(keys)
        # Stop capture promptly even when subscription discovery is in flight.
        # Scheduler rows/dedup remain owned by the supervisor thread.
        with self.job_lock:
            for key in keys:
                if key in self.job_controls:
                    self.job_controls[key].stop()
        self.commands.put(('stop', keys))

    def _discover(self):
        # A fresh client each poll reloads cached rules as they expire. HTTP
        # resources never cross from UI or a recorder into this thread.
        rules = load_rules(self.source, self.control, proxy=self.options.proxy)
        with ApiClient(self.auth, rules, self.control, proxy=self.options.proxy) as client:
            client.user('me')
            return discover_streams(client, self.options)

    def row(self, stream, status, **details):
        self.rows.setdefault(stream.key, {'key':stream.key, 'creator':stream.creator,
                                         'stream_id':stream.stream_id})
        old = dict(self.rows[stream.key])
        self.rows[stream.key].update(status=status, **details)
        if old == self.rows[stream.key]:
            return
        self.emit('watch_row', dict(self.rows[stream.key]))

    def _start(self, stream):
        control = Control()
        self.row(stream, 'Connecting')

        def emit(kind, value):
            # The supervisor serializes status updates, including late events
            # during stop, rather than mutating scheduler state from a worker.
            self.completed.put(('event', stream, (kind, value)))

        def run():
            try:
                result = self.recorder(stream, self.auth, self.options, control, emit)
                self.completed.put(('result', stream, result))
            except Cancelled:
                self.completed.put(('cancelled', stream, None))
            except AppError as exc:
                self.completed.put(('failure', stream, str(exc)))
            except Exception:
                self.completed.put(('failure', stream, 'Browser recording failed; private details withheld.'))

        thread = threading.Thread(target=run, name='ofdl-live-browser', daemon=True)
        self.jobs[stream.key] = (thread, control)
        with self.job_lock:
            self.job_controls[stream.key] = control
        thread.start()

    def _drain(self):
        while not self.commands.empty():
            _, keys = self.commands.get_nowait()
            for key in keys:
                self.finished.add(key)
                if key in self.jobs:
                    self.jobs[key][1].stop()
                if key in self.rows:
                    self.rows[key]['status'] = 'Stopped for this stream'
                    self.emit('watch_row', dict(self.rows[key]))
        while not self.completed.empty():
            kind, stream, value = self.completed.get_nowait()
            if kind == 'event':
                event, detail = value
                if stream.key in self.finished:
                    continue
                if event == 'live_progress':
                    self.row(stream, 'Recording', **detail)
                elif event == 'live_status':
                    self.row(stream, detail)
                continue
            job = self.jobs.pop(stream.key, None)
            with self.job_lock:
                self.job_controls.pop(stream.key, None)
            if job:
                job[0].join()
            if stream.key in self.finished or kind == 'cancelled':
                self.row(stream, 'Stopped for this stream',
                         path=str(value.path) if kind == 'result' and value.path else self.rows[stream.key].get('path',''))
                continue
            if kind == 'result' and value.bytes_written > 0:
                self.finished.add(stream.key)
                self.row(stream, f'Saved · {value.reason}', path=str(value.path), bytes=value.bytes_written)
            else:
                failures = self.failures.get(stream.key, 0) + 1
                self.failures[stream.key] = failures
                self.retry_at[stream.key] = self.clock() + min(300, 60 * 2 ** (failures-1))
                detail = value if kind == 'failure' else 'No complete media received.'
                self.row(stream, ('Failed · retry limit' if failures >= 3 else 'Retry waiting') + ' · ' + detail)

    def step(self, streams):
        """Apply a successful scan. Useful independently for deterministic tests."""
        self._drain()
        for stream in streams:
            self.control.checkpoint()
            key = stream.key
            if key in self.jobs or key in self.finished or self.failures.get(key, 0) >= 3:
                continue
            if self.clock() < self.retry_at.get(key, 0):
                continue
            if len(self.jobs) >= self.options.parallel or any(
                    job_key.split(':',1)[0] == stream.creator for job_key in self.jobs):
                self.row(stream, 'Live · waiting for recording slot')
                continue
            self._start(stream)

    def snapshot(self, streams):
        """Only successful scans count toward offline detection."""
        live = {stream.key for stream in streams}
        for key, (_, control) in self.jobs.items():
            self.missing[key] = 0 if key in live else self.missing.get(key, 0) + 1
            if self.missing[key] >= 2:
                control.stop()
        for key, row in self.rows.items():
            if key not in live and key not in self.jobs and row['status'].startswith('Live ·'):
                row['status'] = 'Offline'
                self.emit('watch_row',dict(row))
        # Long-running monitoring should not accumulate unlimited old rows/IDs.
        for key in list(self.rows):
            if len(self.rows) <= 1000:
                break
            if key not in live and key not in self.jobs:
                del self.rows[key]
                self.finished.discard(key)
                self.failures.pop(key,None)
                self.retry_at.pop(key,None)
                self.missing.pop(key,None)
                self.emit('watch_remove',key)

    def run(self):
        next_scan = 0
        streams = []
        scan_failures = 0
        try:
            while True:
                self.control.checkpoint()
                self._drain()
                now = self.clock()
                if now >= next_scan:
                    self.emit('watch_status', 'Checking active subscriptions for live streams…')
                    try:
                        streams = self.discover()
                        self.snapshot(streams)
                        scan_failures = 0
                        next_scan = self.clock() + self.options.interval
                        self.emit('watch_status', f'Watching · {len(streams)} live · next check in {self.options.interval}s')
                    except Cancelled:
                        raise
                    except ApiError as exc:
                        if exc.status in {401, 403}:
                            raise AppError('Live monitor paused: session rejected. Update Session and enable monitoring again.') from None
                        scan_failures += 1
                        streams = []
                        delay = max(300 if exc.status == 429 else 60, min(900, 60 * 2 ** min(scan_failures, 4)))
                        next_scan = self.clock() + delay
                        self.emit('watch_status', f'Live scan HTTP {exc.status} · retry in {delay}s; running recordings continue')
                    except AppError:
                        scan_failures += 1
                        if scan_failures >= 3:
                            raise
                        streams = []
                        next_scan = self.clock() + 60 * scan_failures
                        self.emit('watch_status', 'Live scan incomplete · retry waiting; running recordings continue')
                self.step(streams)
                self.control.wait(.25)
        except Cancelled:
            pass
        finally:
            for _, control in self.jobs.values():
                control.stop()
            for thread, _ in self.jobs.values():
                thread.join()
            self._drain()
