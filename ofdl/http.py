"""HTTP transport for the account API.

OnlyFans is fronted by Cloudflare. A plain ``requests`` client can be answered with a
403 HTML protection page on some networks even when the imported session is valid,
because its TLS/HTTP fingerprint and header set do not look like a browser's. When the
optional ``curl_cffi`` package is installed, API requests are sent with a Chrome TLS
impersonation profile, which presents a browser-like handshake and header order.

This is a compatibility transport, not a challenge solver: a genuine JavaScript or
managed Cloudflare challenge still blocks the request and is never bypassed. The
public signing-rules download deliberately keeps using plain ``requests``.
"""
from __future__ import annotations

import requests
from urllib.parse import unquote, urlsplit

from .common import AppError

PROXY_SCHEMES = {'http', 'https', 'socks5', 'socks5h'}

try:  # Optional: only improves Cloudflare compatibility; absence is not fatal.
    from curl_cffi import requests as _curl_requests
    from curl_cffi.requests.errors import RequestsError as CurlRequestsError
    HAVE_CURL_CFFI = True
except Exception:  # pragma: no cover - import guard for missing/odd installs
    _curl_requests = None
    CurlRequestsError = None
    HAVE_CURL_CFFI = False

IMPERSONATE = 'chrome'


def normalize_proxy(value: str | None) -> str:
    """Validate one explicit proxy URL and return it unchanged.

    Accepted: http://, https://, socks5:// (local DNS) and socks5h:// (remote
    DNS via the proxy). Credentials are allowed but are never written to the
    plaintext preferences file. A proxy is opt-in; ambient *_PROXY environment
    variables stay ignored unless the user enters one here.
    """
    text = str(value or '').strip()
    if not text:
        return ''
    if len(text) > 512 or any(ord(char) < 33 or ord(char) == 127 for char in text):
        raise AppError('The proxy URL contains spaces or unsupported characters.')
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        raise AppError('The proxy URL is not valid.') from None
    if parsed.scheme.lower() not in PROXY_SCHEMES:
        raise AppError('Use an http://, https://, socks5:// or socks5h:// proxy URL.')
    if not parsed.hostname or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
        raise AppError('The proxy URL must be scheme://host:port without a path, query or fragment.')
    if port is not None and not 1 <= port <= 65535:
        raise AppError('The proxy port must be between 1 and 65535.')
    return text


def proxy_mapping(value: str) -> dict[str, str] | None:
    proxy = normalize_proxy(value)
    return {'http': proxy, 'https': proxy} if proxy else None


def playwright_proxy(value: str) -> dict[str, str] | None:
    proxy = normalize_proxy(value)
    if not proxy:
        return None
    parsed = urlsplit(proxy)
    scheme = 'socks5' if parsed.scheme.lower() == 'socks5h' else parsed.scheme.lower()
    host = parsed.hostname or ''
    if ':' in host:
        host = f'[{host}]'
    server = f'{scheme}://{host}'
    if parsed.port:
        server += f':{parsed.port}'
    options = {'server': server}
    if parsed.username:
        options['username'] = unquote(parsed.username)
    if parsed.password:
        options['password'] = unquote(parsed.password)
    return options

# Headers a real same-origin XHR sends. They are merged with the authentication and
# signing headers; rules may still remove individual optional headers.
BROWSER_HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'priority': 'u=1, i',
}

# Network exceptions from either backend, so retry/SSL handling stays backend-agnostic.
NETWORK_ERRORS = (requests.exceptions.RequestException,) + ((CurlRequestsError,) if CurlRequestsError else ())


class Transport:
    """Send a prepared request through curl_cffi (browser TLS) or plain requests.

    An explicitly injected session is always used as-is, which keeps tests and any
    caller-provided client deterministic and offline-capable.
    """

    def __init__(self, session=None, *, prefer_impersonate: bool = True, proxy: str = ''):
        self.proxy = normalize_proxy(proxy)
        self.using_curl = session is None and prefer_impersonate and HAVE_CURL_CFFI
        if session is not None:
            self.session = session
        elif self.using_curl:
            self.session = _curl_requests.Session()
        else:
            self.session = requests.Session()
        self.session.trust_env = False
        for attribute in ('headers', 'cookies'):
            try:
                getattr(self.session, attribute).clear()
            except Exception:
                pass
        mapping = proxy_mapping(self.proxy)
        if mapping:
            self.session.proxies = mapping

    @property
    def kind(self) -> str:
        return 'curl_cffi' if self.using_curl else 'requests'

    def send(self, prepared, *, timeout, verify: bool = True,
             allow_redirects: bool = False, stream: bool = True):
        mapping = proxy_mapping(self.proxy)
        if self.using_curl:
            timeout = timeout[1] if isinstance(timeout, tuple) else timeout
            options = {'headers': dict(prepared.headers), 'data': prepared.body,
                       'timeout': timeout, 'verify': verify, 'stream': stream,
                       'allow_redirects': allow_redirects, 'impersonate': IMPERSONATE}
            if mapping:
                options['proxies'] = mapping
            return self.session.request(prepared.method, prepared.url, **options)
        return self.session.send(prepared, timeout=timeout, verify=verify,
                                 allow_redirects=allow_redirects, stream=stream)

    def close(self) -> None:
        self.session.close()
