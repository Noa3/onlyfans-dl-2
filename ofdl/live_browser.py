"""Capture accessible live WebRTC playback in a temporary browser context."""
from __future__ import annotations

import base64
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .browser import _browser_failure
from .common import AppError, Cancelled, no_events
from .downloads import safe_component, safe_path
from .http import playwright_proxy
from .live import RecordingResult

CAPTURE_SCRIPT = Path(__file__).with_name('live_capture.js').read_text(encoding='utf-8')


def allowed_browser_request(url, method, body, stream_id):
    """Read/view traffic only on OF. No purchase, tip, subscribe or join POSTs.

    The empty /look heartbeat is the sole allowed account write. If the website
    requires another action, capture fails visibly instead of enabling it.
    External RTC services need their own signaling POSTs, without OF cookies.
    """
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    if host == 'onlyfans.com' or host.endswith('.onlyfans.com'):
        if parsed.scheme != 'https' or parsed.port not in (None, 443):
            return False
        if method in {'GET', 'HEAD', 'OPTIONS'}:
            return True
        return (host == 'onlyfans.com' and method == 'POST' and not parsed.query
                and parsed.path == f'/api2/v2/streams/{stream_id}/look'
                and body in (None, '', '{}'))
    return parsed.scheme in {'https', 'wss'}


def live_page(url, creator):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.hostname == 'onlyfans.com'
            and parsed.path.rstrip('/').lower() == f'/{creator}/live'
            and not parsed.query)


class BrowserOutput:
    """Incremental output; only a confirmed flush may promote .part to .webm."""
    def __init__(self, directory, label, emit=no_events):
        self.directory, self.label, self.emit = Path(directory), label, emit
        self.file = self.part = self.final = None
        self.size = self.chunks = 0
        self.elapsed = 0

    def append(self, data):
        if not isinstance(data, dict) or data.get('error'):
            raise AppError('Browser media capture failed or its buffer overflowed; partial recording kept.')
        chunks = data.get('chunks', [])
        if not isinstance(chunks, list) or len(chunks) > 256:
            raise AppError('Invalid browser recording chunk batch.')
        for encoded in chunks:
            if not isinstance(encoded, str) or len(encoded) > 24 * 1024 * 1024:
                raise AppError('Browser recording chunk exceeded its size limit.')
            try:
                chunk = base64.b64decode(encoded, validate=True)
            except ValueError:
                raise AppError('Invalid browser recording chunk.') from None
            if not chunk:
                continue
            if self.file is None:
                if not chunk.startswith(b'\x1a\x45\xdf\xa3'):
                    raise AppError('Browser did not produce a WebM header.')
                self.directory.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                name = f'{safe_component(self.label)[:80]}_{stamp}_{uuid4().hex[:12]}.webm'
                self.final = safe_path(self.directory, name)
                self.part = safe_path(self.directory, name + '.part')
                self.file = self.part.open('xb')
            self.file.write(chunk)
            self.file.flush()
            self.size += len(chunk)
            self.chunks += 1
        self.elapsed = float(data.get('elapsed', 0))
        if self.size and chunks:
            self.emit('live_progress', {'bytes':self.size, 'segments':self.chunks,
                      'elapsed':self.elapsed, 'media_seconds':self.elapsed,
                      'path':str(self.part), 'audio':bool(data.get('audio'))})

    def close(self):
        if self.file:
            self.file.close()
            self.file = None

    def finish(self, reason):
        self.close()
        if self.part:
            self.part.rename(self.final)
        return RecordingResult(self.final, self.chunks, self.elapsed, self.size, reason)


def capture_page(page, output, control, max_seconds=0, *, expected_creator=None):
    """Record an initialized page; no network-specific logic, also locally testable."""
    deadline = time.monotonic() + 90
    audio_wait = None
    while True:
        control.checkpoint()
        now = time.monotonic()
        if expected_creator and not live_page(page.url, expected_creator):
            if now >= deadline:
                raise AppError('Live page needs login or redirected. Update Session and retry.')
            output.emit('live_status', 'Login/verification needed in recording browser')
            page.wait_for_timeout(1000)
            continue
        state = page.evaluate('(silent) => window.ofdlCapture.start(silent)',
                              audio_wait is not None and now - audio_wait >= 15)
        if state.get('ok'):
            break
        if state.get('waitingAudio') and audio_wait is None:
            audio_wait = now
        if now >= deadline:
            raise AppError('No accessible WebRTC player appeared. Stream may require login, a blocked join action, or use an unsupported player.')
        page.wait_for_timeout(250)
    output.emit('live_status', 'Recording · audio + video' if state.get('audio') else 'Recording · VIDEO ONLY (no audio track)')
    started = last_media = time.monotonic()
    media_time = None
    reason = 'ended'
    try:
        while True:
            if control.stopped.is_set():
                reason = 'stopped'
                break
            data = page.evaluate('() => window.ofdlCapture.drain()')
            output.append(data)
            now = time.monotonic()
            if data.get('ended'):
                break
            if expected_creator and not live_page(page.url, expected_creator):
                raise AppError('Live page navigated away; partial recording kept.')
            if data.get('mediaTime') != media_time and not data.get('muted'):
                last_media, media_time = now, data.get('mediaTime')
            if now - last_media >= 90:
                raise AppError('No live video progress for 90 seconds; partial recording kept.')
            if max_seconds and now - started >= max_seconds:
                reason = 'duration limit'
                break
            page.wait_for_timeout(250)
    finally:
        # Flush on stop AND on failures. Failure still propagates, leaving .part.
        output.append(page.evaluate('() => window.ofdlCapture.stop()'))
    return output.finish(reason)


def record_browser(stream, auth, options, control, emit=no_events):
    try:
        from playwright.sync_api import sync_playwright, Error as PlaywrightError
    except ImportError:
        raise AppError('Browser recording needs setup. Click Set up browser in Live recording.') from None
    auth.validate()
    options.validate()
    if not re.fullmatch(r'[a-z0-9_][a-z0-9_.-]{0,79}', stream.creator) or not re.fullmatch(r'[0-9]{1,24}', stream.stream_id):
        raise AppError('Invalid live stream identity.')
    directory = safe_path(options.output_dir, stream.creator)
    output = BrowserOutput(directory, f'{stream.creator}_{stream.stream_id}', emit)
    try:
        with sync_playwright() as playwright:
            control.checkpoint()
            kwargs = {'headless':False, 'timeout':30000,
                      'args':['--autoplay-policy=no-user-gesture-required',
                              '--disable-background-timer-throttling',
                              '--disable-renderer-backgrounding']}
            if options.channel != 'chromium':
                kwargs['channel'] = options.channel
            if options.proxy:
                kwargs['proxy'] = playwright_proxy(options.proxy)
                kwargs['args'].append('--force-webrtc-ip-handling-policy=disable_non_proxied_udp')
            try:
                browser = playwright.chromium.launch(**kwargs)
            except PlaywrightError as exc:
                raise _browser_failure(exc, launch=True) from None
            try:
                context_options = {'service_workers':'block', 'accept_downloads':False}
                if any(token in auth.user_agent.lower() for token in ('chrome/', 'chromium/', 'edg/')):
                    context_options['user_agent'] = auth.user_agent
                context = browser.new_context(**context_options)
                try:
                    cookies = dict(auth.extra_cookies)
                    cookies.pop('auh_id', None)
                    cookies.update(auth_id=auth.user_id, sess=auth.sess_cookie)
                    context.add_cookies([{'name':name, 'value':value, 'url':'https://onlyfans.com/',
                                          'secure':True, 'sameSite':'Lax'} for name,value in cookies.items()])
                    blocked = [False]
                    def route_request(route):
                        request = route.request
                        if allowed_browser_request(request.url, request.method, request.post_data, stream.stream_id):
                            route.continue_()
                        else:
                            blocked[0] = True
                            route.abort('blockedbyclient')
                    context.route('**/*', route_request)
                    context.add_init_script(CAPTURE_SCRIPT)
                    page = context.new_page()
                    emit('live_status', 'Opening live player in browser…')
                    page.goto(f'https://onlyfans.com/{stream.creator}/live', wait_until='domcontentloaded', timeout=45000)
                    # Allow normal verification/login in this temporary window.
                    deadline = time.monotonic() + 120
                    while not live_page(page.url, stream.creator):
                        control.checkpoint()
                        if time.monotonic() >= deadline:
                            raise AppError('Live browser login timed out. Update Session and enable monitoring again.')
                        emit('live_status', 'Login/verification needed in recording browser')
                        page.wait_for_timeout(1000)
                    # Exact view button only; route guard blocks any account action
                    # other than the stream-specific empty viewing heartbeat.
                    for _ in range(20):
                        control.checkpoint()
                        button = page.get_by_role('button', name=re.compile(r'^join live stream$', re.I))
                        if button.count() and button.first.is_visible():
                            button.first.click(timeout=3000)
                            break
                        if page.locator('video').count():
                            break
                        page.wait_for_timeout(250)
                    try:
                        return capture_page(page, output, control, options.max_seconds,
                                            expected_creator=stream.creator)
                    except AppError:
                        if blocked[0] and not output.size:
                            raise AppError('Player requested a blocked account action. Automatic purchases, subscriptions and join POSTs are disabled.') from None
                        raise
                finally:
                    context.close()
            finally:
                browser.close()
    except Cancelled:
        return output.finish('stopped')
    except OSError:
        raise AppError('Could not write browser recording. Check disk space and folder permissions; partial file kept.') from None
    except PlaywrightError:
        raise AppError('Recording browser closed, navigated or failed; partial file kept. Update Session or browser setup and retry.') from None
    finally:
        output.close()


def setup_browser(control, channel='chromium'):
    """Explicit one-click setup; never install packages on automatic startup."""
    requirements = Path(__file__).resolve().parent.parent / 'requirements-browser.txt'
    commands = [[sys.executable, '-m', 'pip', 'install', '-r', str(requirements)]]
    if channel == 'chromium':
        commands.append([sys.executable, '-m', 'playwright', 'install', 'chromium'])
    for command in commands:
        control.checkpoint()
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 900
        try:
            while process.poll() is None:
                control.wait(.25)
                if time.monotonic() >= deadline:
                    raise AppError('Browser setup timed out. Use the included browser installer and retry.')
            if process.returncode:
                raise AppError('Browser setup failed. Run the included browser installer for details.')
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
    control.checkpoint()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            kwargs = {'headless':True, 'timeout':30000}
            if channel != 'chromium':
                kwargs['channel'] = channel
            browser = playwright.chromium.launch(**kwargs)
            browser.close()
    except Exception:
        raise AppError('Browser dependencies installed, but the selected browser could not start. Choose an installed Chrome/Edge or set up Chromium.') from None
