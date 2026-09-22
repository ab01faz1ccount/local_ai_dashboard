"""
backend/core/net_util.py

Small stdlib-only HTTP helper shared by the online discovery features
(model search/download from Hugging Face, agent search on GitHub).

Its one job beyond "make a request" is enforcing a host allow-list --
both on the URL we ask for and on every redirect it answers with. The
whole point of "only search reputable sites" is defeated if a
compromised or malicious redirect can quietly send a download somewhere
else, so redirects to a host outside the list fail instead of being
followed.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import urlparse

USER_AGENT = "local-ai-control-center/1.0 (+local)"
DEFAULT_TIMEOUT = 20


class DiscoveryError(Exception):
    """User-facing (Persian) error from an online lookup or download.

    `kind` lets the API layer pick a sensible HTTP status:
      "network"   -- can't reach the site (offline, DNS, timeout)
      "not_found" -- the site says it doesn't exist
      "gated"     -- needs a login / license acceptance we can't do
      "rate"      -- the site is rate-limiting us
      "bad_input" -- the request itself was invalid
      "error"     -- anything else
    """

    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


def host_allowed(url: str, allowed_suffixes: tuple[str, ...]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == s or host.endswith("." + s) for s in allowed_suffixes)


class _RestrictedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_suffixes: tuple[str, ...]):
        self._allowed = allowed_suffixes

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not host_allowed(newurl, self._allowed):
            raise urllib.error.URLError(f"redirect to a non-allowed host blocked: {urlparse(newurl).hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(
    url: str,
    *,
    allowed_hosts: tuple[str, ...],
    headers: Optional[dict[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
):
    """Opens `url` and returns the response object (caller closes it).
    Raises DiscoveryError, never a raw urllib exception."""
    if not host_allowed(url, allowed_hosts):
        raise DiscoveryError("آدرس خارج از سایت‌های مجاز بود.", "bad_input")

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    opener = urllib.request.build_opener(_RestrictedRedirectHandler(allowed_hosts))
    try:
        return opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise DiscoveryError("پیدا نشد (۴۰۴).", "not_found") from exc
        if exc.code in (401, 403):
            # GitHub uses 403 for rate limiting too; the header tells them apart.
            if exc.headers is not None and exc.headers.get("X-RateLimit-Remaining") == "0":
                raise DiscoveryError("سرویس موقتاً محدودت کرده (rate limit) — چند دقیقه بعد دوباره امتحان کن.", "rate") from exc
            raise DiscoveryError(
                "این مورد نیاز به ورود یا پذیرش شرایط توی خود سایت داره (gated) — از این‌جا قابل دانلود نیست.",
                "gated",
            ) from exc
        if exc.code == 429:
            raise DiscoveryError("سرویس موقتاً محدودت کرده (rate limit) — چند دقیقه بعد دوباره امتحان کن.", "rate") from exc
        raise DiscoveryError(f"سایت خطا داد ({exc.code}).", "error") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
        raise DiscoveryError("به سایت وصل نشد — اینترنتت رو چک کن.", "network") from exc


def get_json(
    url: str,
    *,
    allowed_hosts: tuple[str, ...],
    headers: Optional[dict[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    with open_url(
        url, allowed_hosts=allowed_hosts, headers={"Accept": "application/json", **(headers or {})}, timeout=timeout
    ) as resp:
        try:
            return json.loads(resp.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError, OSError) as exc:
            raise DiscoveryError("جواب سایت قابل خوندن نبود.", "error") from exc


def get_text(
    url: str,
    *,
    allowed_hosts: tuple[str, ...],
    headers: Optional[dict[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = 512_000,
) -> str:
    with open_url(url, allowed_hosts=allowed_hosts, headers=headers, timeout=timeout) as resp:
        try:
            return resp.read(max_bytes).decode("utf-8", errors="replace")
        except OSError as exc:
            raise DiscoveryError("جواب سایت قابل خوندن نبود.", "network") from exc
