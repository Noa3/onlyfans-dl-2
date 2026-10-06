"""Explicit, local session import. Imported text is NEVER executed."""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit

MAX_IMPORT_BYTES = 10 * 1024 * 1024
_COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class SessionError(ValueError):
    """An actionable message containing no supplied secret values."""


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _normal(name: str) -> str:
    return name.strip().lower().replace('_', '-')


def is_api_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname in {'onlyfans.com', 'www.onlyfans.com'}
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and parsed.path.startswith('/api2/v2/'))
    except ValueError:
        return False


def parse_cookies(value: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in value.split(';'):
        if '=' not in part:
            continue
        name, token = part.strip().split('=', 1)
        token = token.strip()
        if token.startswith('"') and token.endswith('"'):
            token = token[1:-1]
        if not _COOKIE_NAME.fullmatch(name):
            raise SessionError('The Cookie header contains an invalid cookie name.')
        if any(ord(c) < 32 or ord(c) > 126 for c in token):
            raise SessionError('Cookie values must not contain control characters.')
        if name in cookies and cookies[name] != token:
            raise SessionError('Conflicting cookies were supplied. Copy one request from one account.')
        cookies[name] = token
    return cookies


@dataclass(frozen=True, repr=False)
class Credentials:
    user_id: str
    user_agent: str
    x_bc: str
    sess_cookie: str
    extra_cookies: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:
        return 'Credentials(<redacted>)'

    def validate(self) -> Credentials:
        fields = {'USER_ID': self.user_id, 'USER_AGENT': self.user_agent,
                  'X_BC': self.x_bc, 'SESS_COOKIE': self.sess_cookie}
        missing = [key for key, value in fields.items() if not isinstance(value, str) or not value.strip()]
        if missing:
            raise SessionError('Missing ' + ', '.join(missing) + '. Copy a logged-in API request including its Cookie header.')
        if not re.fullmatch(r'[1-9][0-9]{0,23}', self.user_id):
            raise SessionError('USER_ID must be your numeric, logged-in account ID, not a creator username.')
        for key, value in fields.items():
            if len(value) > 16384 or any(ord(c) < 32 or ord(c) > 126 for c in value):
                raise SessionError(key + ' contains invalid characters or is too long.')
        if ';' in self.sess_cookie or re.search(r'\s', self.sess_cookie):
            raise SessionError('SESS_COOKIE needs only the sess value, not a complete Cookie header.')
        if ';' in self.x_bc or re.search(r'\s', self.x_bc):
            raise SessionError('X_BC must be the value of the x-bc request header.')
        if len(self.extra_cookies) > 100:
            raise SessionError('Too many cookies were supplied.')
        for name, token in self.extra_cookies.items():
            if not isinstance(name, str) or not _COOKIE_NAME.fullmatch(name):
                raise SessionError('An extra cookie name is invalid.')
            if not isinstance(token, str) or len(token) > 16384 or any(ord(c) < 32 or ord(c) > 126 for c in token) or ';' in token:
                raise SessionError('An extra cookie value is invalid.')
        if self.extra_cookies.get('auth_id', self.user_id) != self.user_id:
            raise SessionError('The user-id header and auth_id cookie do not match. Copy one request from one account.')
        if self.extra_cookies.get('sess', self.sess_cookie) != self.sess_cookie:
            raise SessionError('Conflicting session cookies were supplied.')
        return self

    def cookie_header(self) -> str:
        self.validate()
        cookies = {k: v for k, v in self.extra_cookies.items() if k not in {'auth_id', 'sess', 'auh_id'}}
        cookies.update({'auth_id': self.user_id, 'sess': self.sess_cookie})
        return '; '.join(f'{key}={value}' for key, value in cookies.items())

    def to_dict(self) -> dict[str, Any]:
        """For an explicitly requested OS-keychain write only, never preferences/logs."""
        return {'user_id':self.user_id, 'user_agent':self.user_agent, 'x_bc':self.x_bc,
                'sess_cookie':self.sess_cookie, 'extra_cookies':dict(self.extra_cookies)}

    @classmethod
    def from_headers(cls, headers: Mapping[str, Any]) -> Credentials:
        values = {_normal(str(k)): _clean(v) for k, v in headers.items()}
        cookies = parse_cookies(values.get('cookie', ''))
        # auth_id is the real cookie name. Accept the old script's typo only as an import fallback.
        uid = values.get('user-id') or values.get('auth-id') or cookies.get('auth_id') or cookies.get('auh_id', '')
        return cls(uid, values.get('user-agent',''), values.get('x-bc',''),
                   values.get('sess-cookie') or values.get('sess') or cookies.get('sess',''), cookies).validate()


def _header_pairs(items: Any) -> dict[str, str]:
    if isinstance(items, dict):
        return {str(k): _clean(v) for k, v in items.items()}
    if not isinstance(items, list):
        raise SessionError('Expected a request-header object or a list of name/value entries.')
    return {str(row['name']): _clean(row.get('value')) for row in items
            if isinstance(row, dict) and isinstance(row.get('name'), str)}


def _json_session(data: Any) -> Credentials:
    if not isinstance(data, dict):
        raise SessionError('The imported JSON must contain a session, request headers, or a HAR log.')
    if isinstance(data.get('log'), dict):
        entries = data['log'].get('entries', [])
        if not isinstance(entries, list):
            raise SessionError('The HAR log has no valid request entries.')
        for entry in reversed(entries):
            request = entry.get('request', {}) if isinstance(entry, dict) else {}
            if not isinstance(request, dict) or not is_api_url(str(request.get('url', ''))):
                continue
            try:
                headers = _header_pairs(request.get('headers', []))
                if not any(k.lower() == 'cookie' for k in headers):
                    cookies = _header_pairs(request.get('cookies', []))
                    headers['Cookie'] = '; '.join(f'{k}={v}' for k,v in cookies.items())
                return Credentials.from_headers(headers)
            except SessionError:
                continue
        raise SessionError('No complete logged-in OnlyFans API request found. The HAR may be sanitized and missing cookies; paste a cURL request instead.')
    if isinstance(data.get('auth'), dict):
        data = data['auth']
    if 'headers' in data:
        if 'url' in data and not is_api_url(str(data['url'])):
            raise SessionError('Import a request to the HTTPS OnlyFans API, not another website.')
        return Credentials.from_headers(_header_pairs(data['headers']))
    if isinstance(data.get('extra_cookies'), dict):
        extras = data['extra_cookies']
        headers = {_normal(str(k)):_clean(v) for k,v in data.items() if k != 'extra_cookies'}
        headers['cookie'] = '; '.join(f'{k}={v}' for k,v in extras.items())
        return Credentials.from_headers(headers)
    return Credentials.from_headers(data)


def _curl_headers(text: str) -> dict[str, str]:
    # Normalize shell line continuations. shlex only tokenizes; no shell/eval/exec is used.
    text = re.sub(r'(?:\\|\^|`)\r?\n', ' ', text)
    text = text.replace('^"', '\\"')
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        raise SessionError('Unable to parse the copied cURL text. Use “Copy as cURL (bash)” or paste raw request headers.') from None
    headers: dict[str, str] = {}
    urls: list[str] = []
    i = 1
    while i < len(tokens):
        token = tokens[i]
        flag, equals, inline = token.partition('=')
        if flag in {'-H', '--header', '-b', '--cookie', '-A', '--user-agent', '--url'}:
            if equals:
                value = inline
            else:
                i += 1
                if i >= len(tokens):
                    raise SessionError('A copied cURL option is missing its value.')
                value = tokens[i]
            if flag in {'-H','--header'} and ':' in value:
                key, val = value.split(':', 1)
                headers[key.strip().lower()] = val.strip()
            elif flag in {'-b','--cookie'}:
                headers['cookie'] = value
            elif flag in {'-A','--user-agent'}:
                headers['user-agent'] = value
            elif flag == '--url':
                urls.append(value)
        elif token.startswith(('https://', 'http://')):
            urls.append(token)
        i += 1
    if not urls or any(not is_api_url(url) for url in urls):
        raise SessionError('Copy one request to https://onlyfans.com/api2/v2/ from your own logged-in browser.')
    return headers


def parse_session(text: str) -> Credentials:
    if not isinstance(text, str) or not text.strip():
        raise SessionError('Paste a copied cURL request, raw headers, or session JSON first.')
    if len(text.encode('utf-8')) > MAX_IMPORT_BYTES:
        raise SessionError('The import exceeds 10 MB. Copy one API request instead of a complete HAR.')
    text = text.lstrip('\ufeff').strip()
    if text.startswith(('{','[')):
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            raise SessionError('The imported JSON is not valid.') from None
        return _json_session(data)
    if re.match(r'^curl(?:\.exe)?\s', text, flags=re.I):
        return Credentials.from_headers(_curl_headers(text))
    headers: dict[str, str] = {}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    i = 0
    recognized = {'user-agent','user-id','x-bc','cookie','auth-id','sess','sess-cookie'}
    while i < len(lines):
        line = lines[i]
        if ':' in line:
            name, value = line.split(':', 1)
            if _normal(name) in recognized:
                if value.strip():
                    headers[_normal(name)] = value.strip()
                elif i + 1 < len(lines):
                    i += 1
                    headers[_normal(name)] = lines[i]
        elif _normal(line) in recognized and i + 1 < len(lines):
            i += 1
            headers[_normal(line)] = lines[i]
        i += 1
    return Credentials.from_headers(headers)


SERVICE_NAME = 'OnlyFansDL-Desktop'
KEYCHAIN_ACCOUNT = 'session-v2'
_TRUSTED_BACKEND_MODULES = {
    'keyring.backends.Windows', 'keyring.backends.macOS',
    'keyring.backends.SecretService', 'keyring.backends.kwallet',
}


def _secure_backend():
    try:
        import keyring
        backend = keyring.get_keyring()
        candidates = list(backend.backends) if type(backend).__module__ == 'keyring.backends.chainer' else [backend]
        for candidate in candidates:
            if type(candidate).__module__ in _TRUSTED_BACKEND_MODULES and candidate.priority > 0:
                return candidate
    except Exception:
        pass
    raise SessionError('No supported OS credential store is available. Install keyring and unlock your OS keychain, or keep the session in memory. Plaintext fallback is disabled.')


def save_session(auth: Credentials) -> None:
    auth.validate()
    backend = _secure_backend()
    try:
        backend.set_password(SERVICE_NAME, KEYCHAIN_ACCOUNT, json.dumps(auth.to_dict()))
    except Exception:
        raise SessionError('The OS credential store refused the save. No plaintext session file was written.') from None


def load_session() -> Credentials:
    backend = _secure_backend()
    try:
        value = backend.get_password(SERVICE_NAME, KEYCHAIN_ACCOUNT)
    except Exception:
        raise SessionError('Cannot unlock or read the OS credential store.') from None
    if not value:
        raise SessionError('There is no saved session in the OS credential store.')
    return parse_session(value)


def forget_session() -> None:
    backend = _secure_backend()
    try:
        if backend.get_password(SERVICE_NAME, KEYCHAIN_ACCOUNT) is not None:
            backend.delete_password(SERVICE_NAME, KEYCHAIN_ACCOUNT)
    except Exception:
        raise SessionError('Cannot remove the session from the OS credential store.') from None
