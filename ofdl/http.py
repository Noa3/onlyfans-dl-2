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

try:  # Optional: only improves Cloudflare compatibility; absence is not fatal.
    from curl_cffi import requests as _curl_requests
    from curl_cffi.requests.errors import RequestsError as CurlRequestsError
    HAVE_CURL_CFFI = True
except Exception:  # pragma: no cover - import guard for missing/odd installs
    _curl_requests = None
    CurlRequestsError = None
    HAVE_CURL_CFFI = False

IMPERSONATE = 'chrome'

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
    """Send a prepared GET through curl_cffi (browser TLS) or plain requests.

    An explicitly injected session is always used as-is, which keeps tests and any
    caller-provided client deterministic and offline-capable.
    """

    def __init__(self, session=None, *, prefer_impersonate: bool = True):
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

    @property
    def kind(self) -> str:
        return 'curl_cffi' if self.using_curl else 'requests'

    def send(self, prepared, *, timeout, verify: bool = True,
             allow_redirects: bool = False, stream: bool = True):
        if self.using_curl:
            timeout = timeout[1] if isinstance(timeout, tuple) else timeout
            return self.session.request('GET', prepared.url, headers=dict(prepared.headers),
                                        timeout=timeout, verify=verify, stream=stream,
                                        allow_redirects=allow_redirects, impersonate=IMPERSONATE)
        return self.session.send(prepared, timeout=timeout, verify=verify,
                                 allow_redirects=allow_redirects, stream=stream)

    def close(self) -> None:
        self.session.close()
