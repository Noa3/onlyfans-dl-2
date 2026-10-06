"""Opt-in capture from a new visible browser, not the user's existing browser profile."""
from __future__ import annotations
import time
from typing import Any
from urllib.parse import urlsplit
from .auth import Credentials, SessionError, is_api_url
from .common import AppError, Cancelled, Control, EventSink, no_events


def credentials_from_response(response: Any) -> Credentials | None:
    """Check a completed own-account GET; never read login POST bodies/passwords.

    A generic HTTP 200 is not evidence of a logged-in account. The account JSON
    must identify the same user as the request credentials. This does not prove
    that Requests (outside the browser) can reuse that session.
    """
    if (not is_api_url(response.url) or response.status != 200 or response.request.method != 'GET'
            or urlsplit(response.url).path.rstrip('/') != '/api2/v2/users/me'):
        return None
    mime = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if mime != 'application/json' and not mime.endswith('+json'):
        return None
    try:
        auth = Credentials.from_headers(response.request.all_headers())
        data = response.json()
        if not isinstance(data, dict) or data.get('error') or str(data.get('id', '')) != auth.user_id:
            return None
        return auth
    except (SessionError, ValueError, UnicodeError, RecursionError):
        return None


def _browser_failure(error: Exception, *, launch: bool) -> AppError:
    """Classify library errors without returning their paths, URLs, or raw text."""
    stage = 'browser-launch' if launch else 'browser-capture'
    text = str(error).lower()
    if "executable doesn't exist" in text or ('distribution' in text and 'not found' in text):
        category = 'missing-browser'
        advice = 'The selected browser executable was not found. Run install_browser_windows.bat (Windows) or install_browser.sh, or choose an installed Chrome/Edge.'
    elif 'timeout' in text or 'timed out' in text:
        category = 'timeout'
        advice = 'The browser operation timed out. Complete normal verification in your browser and use Paste copied request, or retry the helper.'
    elif 'has been closed' in text or 'target closed' in text:
        category = 'browser-closed'
        advice = 'The helper browser/page was closed or disconnected before capture completed. Keep it open through capture, or use Paste copied request.'
    else:
        category = 'unknown'
        advice = ('The selected browser could not start. Check the browser-helper installation or use Paste copied request.' if launch else
                  'Browser capture was interrupted. Retry or use Paste copied request from your normal browser.')
    return AppError(f'{advice}\nSafe diagnostic: stage={stage}; category={category}. Raw browser error details were withheld.')


def capture_context(context: Any, control: Control, *, timeout: float = 300,
                    emit: EventSink = no_events) -> Credentials:
    captured: list[Credentials] = []
    page = context.new_page()

    def on_finished(request: Any) -> None:
        if captured or control.stopped.is_set():
            return
        try:
            if request.method != 'GET' or not is_api_url(request.url):
                return
            response = request.response()
            auth = credentials_from_response(response) if response is not None else None
            if auth:
                captured.append(auth)
        except Exception:
            # Closing a page can invalidate Playwright handles. Never print their contents.
            return

    context.on('requestfinished',on_finished)
    try:
        emit('log','A temporary browser is opening. Log into your own account, complete normal verification, then open your home feed.')
        try:
            page.goto('https://onlyfans.com/',wait_until='domcontentloaded',timeout=45000)
        except Exception:
            if page.is_closed():
                raise AppError('The browser window was closed before session capture finished.') from None
            emit('log','The page is still loading. You can complete login in the browser, or stop and use request import.')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            control.checkpoint()
            if captured:
                emit('log','Session captured from a completed own-account response. The browser will close; Test session checks reuse by the desktop client separately.')
                return captured[0]
            pages = [item for item in context.pages if not item.is_closed()]
            if not pages:
                raise AppError('The browser was closed before a complete logged-in API request was captured.')
            pages[0].wait_for_timeout(200)
        raise AppError('No matching own-account response was captured in time. Open/reload your home feed or use Paste copied request from your normal logged-in browser. Safe diagnostic: stage=browser-capture; category=timeout.')
    finally:
        try:
            context.remove_listener('requestfinished',on_finished)
        except Exception:
            pass  # The browser may already be gone; do not replace the original result/error.


def capture_session(control: Control, *, channel: str = 'chromium',
                    emit: EventSink = no_events) -> Credentials:
    try:
        from playwright.sync_api import sync_playwright, Error as PlaywrightError
    except ImportError:
        raise AppError('Browser helper is not installed. Run the browser-helper installer included in this folder, or use Paste copied request. Safe diagnostic: stage=browser-launch; category=missing-helper.') from None
    if channel not in {'chromium','chrome','msedge'}:
        raise AppError('Select Chromium, Google Chrome, or Microsoft Edge.')
    try:
        with sync_playwright() as playwright:
            control.checkpoint()
            try:
                kwargs = {'headless':False,'timeout':30000}
                if channel != 'chromium':
                    kwargs['channel'] = channel
                browser = playwright.chromium.launch(**kwargs)
            except PlaywrightError as exc:
                raise _browser_failure(exc, launch=True) from None
            try:
                # No persistent user-data directory, saved passwords, storage_state, or traces.
                context = browser.new_context()
                try:
                    return capture_context(context,control,emit=emit)
                finally:
                    try:
                        context.close()
                    except PlaywrightError:
                        emit('log','The helper context was already closed or could not finish closing. Raw cleanup details were withheld.')
            finally:
                try:
                    browser.close()
                except PlaywrightError:
                    emit('log','The helper browser was already closed or could not finish closing. Close any remaining helper window manually.')
    except (AppError,Cancelled):
        raise
    except PlaywrightError as exc:
        raise _browser_failure(exc, launch=False) from None
