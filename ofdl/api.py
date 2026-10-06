"""Signed GET requests, public rule loading, and defensive pagination.

The endpoint families originate in the supplied script. Their live availability is
not guaranteed: this is an unofficial, read-only client, not a platform API contract.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import unquote, urlsplit

import requests

from .auth import Credentials
from .common import AppError, Control, EventSink, no_events
from .settings import atomic_json, config_dir
from .diagnostics import ErrorDetails, clock_hint, details_from_json, inspect_error_response

API_ORIGIN = 'https://onlyfans.com'
API_URL = API_ORIGIN + '/api2/v2'
TIMEOUT = (10, 30)
RULE_CACHE_SECONDS = 900
MAX_RULE_BYTES = 512 * 1024
MAX_API_BYTES = 32 * 1024 * 1024
MAX_RETRY_WAIT = 300


class ApiError(AppError):
    def __init__(self, status: int, *, details: ErrorDetails | None = None, diagnostic: str = ''):
        self.status = status
        advice = {
            400: 'Refresh signing rules, check the system clock, and re-import your session if needed.',
            401: 'The session was rejected. Log in again and import a fresh session.',
            403: 'Access was refused. Check account access and signing rules, or refresh your session. Browser security challenges are not bypassed.',
            404: 'The profile/content was not found, or this endpoint has changed.',
            429: 'The server is rate-limiting requests. Stop and try later.',
        }.get(status, 'The remote service did not accept the request.')
        if details is not None:
            if details.category == 'invalid-signature':
                advice = 'The server reported a signature rejection. Refresh rules once, then retest. Repeated rejection can require a client update; login alone may not fix it.'
            elif details.category == 'refresh-required':
                advice = 'The server requested a refresh without identifying the cause. Refresh rules once and retest; this message does not prove your credentials are wrong.'
            elif details.response_type == 'html':
                advice = 'An HTML page was returned instead of API JSON. A login/protection page or service error may be responsible. Use a normal browser; challenges are not bypassed.'
            elif details.category == 'authentication-rejected':
                advice = 'The server reported an authentication rejection. Import a fresh session from your normal logged-in browser.'
        prefix = 'API returned an error object (HTTP 200)' if status == 200 else f'HTTP {status}'
        message = f'{prefix}. {advice}'
        if details is not None:
            message += f'\nSafe diagnostic: stage=api; status={status}; {details.summary()}'
            if diagnostic:
                message += '; ' + diagnostic
        super().__init__(message)


def retry_delay(value: str | None, now: float | None = None) -> float | None:
    if not value:
        return None
    now = time.time() if now is None else now
    try:
        numeric = float(value)
        if numeric >= 0 and numeric < float('inf'):
            return numeric
    except ValueError:
        pass
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0.0, date.timestamp() - now)
    except (TypeError, ValueError, OverflowError):
        return None


def wait_retry(control: Control, attempt: int, header: str | None, emit: EventSink) -> None:
    delay = retry_delay(header)
    delay = delay if delay is not None else min(30.0, 2.0 ** attempt)
    if delay > MAX_RETRY_WAIT:
        raise AppError('The server requested a long retry delay. This run is stopped rather than retrying earlier than requested.')
    emit('log', f'Temporary network/server issue; retrying after {delay:.0f} seconds. Stop remains available.')
    control.wait(delay)


def _read_json(response: requests.Response, maximum: int, control: Control) -> Any:
    pieces: list[bytes] = []
    count = 0
    for chunk in response.iter_content(64 * 1024):
        control.checkpoint()
        count += len(chunk)
        if count > maximum:
            raise AppError('The server response is larger than the configured safety limit.')
        pieces.append(chunk)
    try:
        return json.loads(b''.join(pieces).decode('utf-8-sig'))
    except (ValueError, UnicodeError, RecursionError):
        raise AppError('Expected JSON, but received a different response. A login page, security challenge, or API change may be responsible.') from None


@dataclass(frozen=True)
class Rules:
    static_param: str
    format: str
    checksum_indexes: tuple[int, ...]
    checksum_constant: int
    app_token: str
    remove_headers: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, values: Any) -> Rules:
        try:
            if not isinstance(values, dict):
                raise ValueError
            static = values['static_param']
            if not isinstance(static, str) or not 1 <= len(static) <= 4096 or any(ord(c) < 32 or ord(c) > 126 for c in static):
                raise ValueError
            fmt = values.get('format')
            if fmt is None:
                fmt = f"{values['prefix']}:{{}}:{{:x}}:{values['suffix']}"
            # No arbitrary Python format fields/attribute lookups from downloaded JSON.
            if not isinstance(fmt, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}:\{\}:\{:x\}:[A-Za-z0-9_-]{1,128}', fmt):
                raise ValueError
            indexes = values['checksum_indexes']
            if not isinstance(indexes, list) or not 1 <= len(indexes) <= 512:
                raise ValueError
            if any(type(index) is not int or not 0 <= index < 40 for index in indexes):
                raise ValueError
            constant = values['checksum_constant']
            if type(constant) is not int or abs(constant) > 10_000_000:
                raise ValueError
            token = values.get('app_token', '33d57ade8c02dbc5a333db99ff9ae26a')
            if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', token):
                raise ValueError
            removed = values.get('remove_headers', [])
            if not isinstance(removed, list) or len(removed) > 32 or any(not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',name) for name in removed):
                raise ValueError
            # Public rules may remove optional headers but must not erase authentication.
            # HTTP names are case-insensitive, but '_' and '-' are distinct.
            removed = tuple(name.lower() for name in removed)
            if set(removed) & {'cookie','sign','time','x-bc','user-agent','app-token','host'}:
                raise ValueError
            return cls(static, fmt, tuple(indexes), constant, token, removed)
        except (KeyError, TypeError, ValueError):
            raise AppError('Signing-rules JSON has an unsupported or invalid schema. Choose a current, trusted rules source.') from None

    @property
    def fingerprint(self) -> str:
        """Public rule-content identifier, never derived from account credentials."""
        content = [self.static_param, self.format, self.checksum_indexes,
                   self.checksum_constant, self.app_token, self.remove_headers]
        return hashlib.sha256(json.dumps(content, separators=(',', ':')).encode()).hexdigest()[:12]

    def signature(self, path: str, timestamp: str, user_id: str) -> str:
        message = '\n'.join([self.static_param, timestamp, path, user_id])
        # SHA-1 here reproduces the API request format; it is not used to secure stored secrets.
        digest = hashlib.sha1(message.encode('utf-8'), usedforsecurity=False).hexdigest()
        checksum = sum(ord(digest[index]) for index in self.checksum_indexes) + self.checksum_constant
        return self.format.format(digest, abs(checksum))


def load_rules(source: str, control: Control, *, force: bool = False,
               cache_dir: Path | None = None, session: requests.Session | None = None,
               emit: EventSink = no_events) -> Rules:
    source = source.strip()
    if not source:
        raise AppError('Choose a signing-rules JSON file or HTTPS source in the Session tab.')
    if '://' not in source:
        try:
            path = Path(source).expanduser()
            if path.stat().st_size > MAX_RULE_BYTES:
                raise AppError('The signing-rules file is too large.')
            data = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError, RecursionError):
            raise AppError('Cannot read the local signing-rules JSON file.') from None
        return Rules.from_dict(data)
    try:
        url = urlsplit(source)
        if url.scheme != 'https' or not url.hostname or url.port not in (None,443) or url.username or url.password or url.query or url.fragment:
            raise ValueError
    except ValueError:
        raise AppError('Rules sources must be HTTPS URLs without credentials, query strings, or fragments, or a local JSON file.') from None
    cache_root = cache_dir or config_dir() / 'rules'
    cache = cache_root / (hashlib.sha256(source.encode()).hexdigest() + '.json')
    if not force and cache.exists():
        try:
            if cache.stat().st_size <= MAX_RULE_BYTES:
                stored = json.loads(cache.read_text(encoding='utf-8'))
                age = time.time() - float(stored['fetched_at'])
                if stored.get('source') == source and 0 <= age < RULE_CACHE_SECONDS:
                    return Rules.from_dict(stored['rules'])
        except (OSError, ValueError, TypeError, KeyError, AppError, RecursionError):
            pass
    control.checkpoint()
    emit('log','Fetching public signing rules using a separate request without account credentials.')
    owns_session = session is None
    public = session or requests.Session()
    public.trust_env = False  # Avoid implicit .netrc credentials and ambient proxy authentication.
    public.headers.clear()
    public.cookies.clear()
    try:
        for attempt in range(3):
            control.checkpoint()
            try:
                with public.get(source, headers={'Accept':'application/json','User-Agent':'OnlyFansDL/2.0 rules-loader'},
                                timeout=TIMEOUT, verify=True, allow_redirects=False, stream=True) as response:
                    if response.status_code == 429 or 500 <= response.status_code <= 599:
                        status = response.status_code
                        header = response.headers.get('Retry-After')
                        if attempt == 2:
                            raise AppError(f'The public rules source returned HTTP {status}. Choose a different source or a local JSON file.')
                    elif response.status_code != 200:
                        raise AppError(f'The public rules source returned HTTP {response.status_code}. Redirects are not followed; provide its final HTTPS JSON URL.')
                    else:
                        data = _read_json(response, MAX_RULE_BYTES, control)
                        rules = Rules.from_dict(data)
                        try:
                            atomic_json(cache, {'source':source,'fetched_at':time.time(),'rules':data})
                        except OSError:
                            emit('log','Rules loaded, but their nonsecret cache could not be saved.')
                        return rules
            except requests.exceptions.SSLError:
                raise AppError('TLS certificate verification failed for the rules source. Certificate checks have not been disabled.') from None
            except requests.RequestException:
                if attempt == 2:
                    raise AppError('Could not reach the rules source. Check your connection or choose a local JSON file.') from None
                header = None
            wait_retry(control, attempt, header, emit)
    finally:
        if owns_session:
            public.close()
    raise AppError('Could not load signing rules.')


class ApiClient:
    def __init__(self, auth: Credentials, rules: Rules, control: Control, *,
                 session: requests.Session | None = None, interval: float = 0.6,
                 retries: int = 3, emit: EventSink = no_events):
        self.auth = auth.validate()
        self.rules = rules
        self.control = control
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.interval = max(0.0, interval)
        self.retries = max(0, min(5, retries))
        self.emit = emit
        self.last_request = 0.0
        if 'user-id' in self.rules.remove_headers:
            self.emit('log', 'Rules requested removal of user-id; retained because this client signs with the authenticated user-id. No account values are logged.')

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _api_error(self, response: requests.Response, prepared: requests.PreparedRequest,
                   details: ErrorDetails | None = None) -> ApiError:
        if details is None:
            details = inspect_error_response(response, self.control)
        identity = 'present' if prepared.headers.get('user-id') == self.auth.user_id else 'missing-or-mismatched'
        diagnostic = (f'user-id-header={identity}; time-unit=milliseconds; rules={self.rules.fingerprint}; '
                      f'clock={clock_hint(response.headers.get("Date"), time.time())}')
        return ApiError(response.status_code, details=details, diagnostic=diagnostic)

    def request(self, endpoint: str, params: Mapping[str, Any] | None = None) -> Any:
        if (not isinstance(endpoint,str) or not endpoint.startswith('/') or endpoint.startswith('//')
                or any(char in endpoint for char in '?\\#\r\n')
                or any(part in {'.','..'} for part in unquote(endpoint).split('/'))):
            raise AppError('Refused an invalid API endpoint.')
        for attempt in range(self.retries + 1):
            self.control.checkpoint()
            self.control.wait(max(0.0, self.last_request + self.interval - time.monotonic()))
            headers = {'Accept':'application/json, text/plain, */*','User-Agent':self.auth.user_agent,
                       'x-bc':self.auth.x_bc,'user-id':self.auth.user_id,'Cookie':self.auth.cookie_header(),
                       'app-token':self.rules.app_token,'Referer':API_ORIGIN + '/', 'Accept-Encoding':'gzip, deflate'}
            prepared = self.session.prepare_request(requests.Request('GET', API_URL + endpoint,
                                                                       params=params, headers=headers))
            # Keep the identity used by this authenticated client's signing contract.
            # Some providers publish removal metadata intended for other clients.
            # Apply optional removals first; never sign with an ID then delete it.
            for name in self.rules.remove_headers:
                if name != 'user-id':
                    prepared.headers.pop(name, None)
            stamp = str(int(time.time() * 1000))
            prepared.headers['sign'] = self.rules.signature(prepared.path_url, stamp, prepared.headers['user-id'])
            prepared.headers['time'] = stamp
            self.last_request = time.monotonic()
            try:
                with self.session.send(prepared, timeout=TIMEOUT, verify=True, allow_redirects=False, stream=True) as response:
                    status = response.status_code
                    if status == 429 or 500 <= status <= 599:
                        if attempt >= self.retries:
                            raise self._api_error(response, prepared)
                        header = response.headers.get('Retry-After')
                    elif status != 200:
                        raise self._api_error(response, prepared)
                    else:
                        data = _read_json(response, MAX_API_BYTES, self.control)
                        if isinstance(data,dict) and data.get('error'):
                            raise self._api_error(response, prepared, details_from_json(data))
                        return data
            except requests.exceptions.SSLError:
                raise AppError('TLS certificate verification failed. Check your clock and certificate setup; verification is still enabled.') from None
            except requests.RequestException:
                if attempt >= self.retries:
                    raise AppError('The API connection failed or timed out after its retry limit.') from None
                header = None
            wait_retry(self.control, attempt, header, self.emit)
        raise AppError('The API request could not be completed.')

    def user(self, name: str = 'me') -> dict[str, Any]:
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',name) or name in {'.','..'}:
            raise AppError('Invalid account name.')
        data = self.request('/users/' + name)
        if not isinstance(data,dict) or not str(data.get('id','')).isdigit():
            raise AppError('The profile response did not contain an account ID. The API format may have changed.')
        if name == 'me' and str(data['id']) != self.auth.user_id:
            raise AppError('The returned account does not match the imported session. Import all session fields together from one current logged-in request; account values were withheld.')
        return data

    def paginate(self, endpoint: str, kind: str, *, since: datetime | None = None) -> Iterator[dict[str,Any]]:
        if kind not in {'posts','archived','stories','messages','purchased','subscriptions'}:
            raise AppError('Unsupported content category.')
        params: dict[str,Any] = {'limit':50}
        if kind == 'messages':
            params['order'] = 'desc'
        elif kind != 'subscriptions':
            params['order'] = 'publish_date_asc'
        if kind == 'subscriptions':
            params['type'] = 'active'
        if kind in {'posts','archived'} and since is not None:
            params['afterPublishTime'] = f'{since.timestamp():.6f}'
        seen: set[str] = set()
        last_cursor: str | None = None
        offset = 0
        for page_number in range(1,10001):
            self.control.checkpoint()
            data = self.request(endpoint, params)
            if isinstance(data,list):
                rows, has_more = data, None
            elif isinstance(data,dict) and isinstance(data.get('list'),list):
                rows, has_more = data['list'], data.get('hasMore')
                if has_more is not None and not isinstance(has_more,bool):
                    raise AppError('Invalid pagination metadata. The API response format may have changed.')
            else:
                raise AppError('Unexpected content-list format. Stopped rather than treating an API change as an empty result.')
            if any(not isinstance(row,dict) or row.get('id') is None for row in rows):
                raise AppError('A content-list entry is malformed or missing its ID.')
            if not rows:
                if has_more is True:
                    raise AppError('Pagination returned an empty page while claiming more results. Stopped to prevent an endless loop.')
                return
            new_rows = [row for row in rows if str(row['id']) not in seen]
            if not new_rows:
                raise AppError('Pagination repeated a page without progress. The run is incomplete; no infinite retry was attempted.')
            self.emit('log',f'{kind.title()}: page {page_number}, {len(rows)} entries received.')
            for row in new_rows:
                key = str(row['id'])
                if key in seen:
                    continue
                seen.add(key)
                yield row
            if has_more is False or (has_more is None and len(rows) < 50):
                return
            if kind == 'messages':
                cursor = str(rows[-1]['id'])
                params['id'] = cursor
            elif kind in {'posts','archived'}:
                raw_cursor = rows[-1].get('postedAtPrecise')
                if raw_cursor is None:
                    raw_date = rows[-1].get('postedAt')
                    try:
                        date = datetime.fromisoformat(str(raw_date).replace('Z','+00:00'))
                        if date.tzinfo is None:
                            date = date.replace(tzinfo=timezone.utc)
                        raw_cursor = f'{date.timestamp():.6f}'
                    except ValueError:
                        raise AppError('The next post-page cursor is missing. This API format needs an update.') from None
                cursor = str(raw_cursor)
                if not re.fullmatch(r'\d+(?:\.\d+)?',cursor):
                    raise AppError('The next post-page cursor is invalid.')
                params['afterPublishTime'] = cursor
            else:
                offset += len(rows)
                cursor = str(offset)
                params['offset'] = offset
            if cursor == last_cursor:
                raise AppError('Pagination cursor stopped advancing. The run is incomplete.')
            last_cursor = cursor
        raise AppError('Pagination exceeded 10,000 pages. Stopped to prevent an unbounded run.')
