"""Small allowlisted HTTP diagnostics; never return server text or request secrets."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from .common import Control

MAX_ERROR_BYTES = 16 * 1024
# Exact normalized messages only. A substring containing a secret is not echoed
# or treated as a recognized message. These labels are observations, not proof
# of which client-side change will fix a rejection.
_MESSAGE_CATEGORIES = {
    'invalid sign': 'invalid-signature',
    'invalid signature': 'invalid-signature',
    'invalid request signature': 'invalid-signature',
    'please refresh the page': 'refresh-required',
    'refresh the page': 'refresh-required',
    'unauthorized': 'authentication-rejected',
    'not authenticated': 'authentication-rejected',
    'invalid session': 'authentication-rejected',
    'session expired': 'authentication-rejected',
    'access denied': 'access-denied',
    'forbidden': 'access-denied',
    'too many requests': 'rate-limited',
}


@dataclass(frozen=True)
class ErrorDetails:
    category: str = 'unknown'
    code: int | None = None
    response_type: str = 'other'
    body_state: str = 'unreadable'

    def summary(self) -> str:
        code = self.code if self.code is not None else 'unavailable'
        return (f'category={self.category}; code={code}; '
                f'response={self.response_type}; body={self.body_state}')


def details_from_json(data: Any) -> ErrorDetails:
    error = data.get('error') if isinstance(data, dict) else None
    category, code = 'unknown', None
    if isinstance(error, dict):
        # A small numeric error code is useful; IDs, arbitrary strings, and booleans
        # are deliberately not copied into the diagnostic.
        raw_code = error.get('code')
        if type(raw_code) is int and 0 <= raw_code <= 9999:
            code = raw_code
        message = error.get('message')
        if isinstance(message, str) and len(message) <= 100:
            normalized = ' '.join(message.lower().split()).rstrip('.!?')
            category = _MESSAGE_CATEGORIES.get(normalized, 'unknown')
    return ErrorDetails(category, code, 'json', 'parsed')


def inspect_error_response(response: requests.Response, control: Control) -> ErrorDetails:
    """Read at most 16 KiB plus one 4 KiB chunk; failures preserve the HTTP error.

    The caller owns and closes the response. Cancellation is not swallowed.
    No body, cookie, URL, arbitrary header, or exception string leaves this function.
    """
    mime = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
    kind = ('json' if mime == 'application/json' or mime.endswith('+json') else
            'html' if mime in {'text/html', 'application/xhtml+xml'} else 'other')
    body = bytearray()
    try:
        for chunk in response.iter_content(4096):
            control.checkpoint()
            if len(body) + len(chunk) > MAX_ERROR_BYTES:
                return ErrorDetails(response_type=kind, body_state='oversized')
            body.extend(chunk)
    except requests.RequestException:
        return ErrorDetails(response_type=kind)
    control.checkpoint()
    if not body:
        return ErrorDetails(response_type=kind, body_state='empty')
    prefix = bytes(body[:100]).lstrip().lower()
    if kind == 'html' or prefix.startswith((b'<!doctype html', b'<html')):
        return ErrorDetails(response_type='html', body_state='withheld')
    try:
        return details_from_json(json.loads(body.decode('utf-8-sig')))
    except (ValueError, UnicodeError, RecursionError):
        return ErrorDetails(response_type=kind)


def clock_hint(server_date: str | None, now: float) -> str:
    """Coarse comparison only; Date is an observation, not a trusted clock service."""
    if not server_date or len(server_date) > 100:
        return 'unknown'
    try:
        date = parsedate_to_datetime(server_date)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        delta = now - date.timestamp()
        if abs(delta) <= 300:
            return 'within-five-minutes'
        return 'local-ahead' if delta > 0 else 'local-behind'
    except (TypeError, ValueError, OverflowError, OSError):
        return 'unknown'
