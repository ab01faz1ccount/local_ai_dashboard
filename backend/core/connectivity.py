"""
backend/core/connectivity.py

A single, cheap connectivity check shared by every feature that needs
the internet (Model Comparison's public-benchmark lookup, authenticity
verification's Hugging Face hash check). Everything else in this app
works fully offline by design -- this module exists so those two
specific features can say so clearly instead of just timing out
silently.

Checks reachability of huggingface.co specifically (the actual host both
features talk to) rather than some arbitrary "is the internet up" target
like 8.8.8.8 -- a captive portal or a corporate proxy that blocks HF but
allows general browsing would otherwise report a false "online".
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request

CONNECTIVITY_CHECK_URL = "https://huggingface.co"
CHECK_TIMEOUT_SECONDS = 3
CACHE_TTL_SECONDS = 15  # avoid a network round-trip on every single call

_lock = threading.Lock()
_last_checked_at = 0.0
_last_result = False


def check_internet(force: bool = False) -> bool:
    """Cheap by default (returns a recent cached result); pass
    `force=True` right before an action that's about to fail confusingly
    offline (e.g. starting a verification) to get a fresh answer."""
    global _last_checked_at, _last_result
    with _lock:
        now = time.monotonic()
        if not force and (now - _last_checked_at) < CACHE_TTL_SECONDS:
            return _last_result
        try:
            req = urllib.request.Request(
                CONNECTIVITY_CHECK_URL, method="HEAD", headers={"User-Agent": "local-ai-control-center"}
            )
            urllib.request.urlopen(req, timeout=CHECK_TIMEOUT_SECONDS)
            _last_result = True
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            _last_result = False
        _last_checked_at = now
        return _last_result
