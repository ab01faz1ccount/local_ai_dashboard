"""
backend/core/security.py

The "do this from day 0, non-negotiable" security requirements:
  1. A random local access token, generated at startup, stored in a local
     config file, required on every API call.
  2. Origin-header checking so only the app's own local frontend can call
     the API from a browser context.
  3. Binding to 127.0.0.1 only -- enforced in main.py at the uvicorn.run()
     call, not here, since that's a startup-time decision, not per-request.

Kept deliberately separate from storage/db.py: the access token guards the
API *before* any DB session is opened, and must keep working even if the
DB is briefly unavailable.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG_PATH = Path("local_config.json")

# Origins the frontend is expected to run on. 5173 = Vite's default dev
# port; 3000 covers a plain `npm start` / build-preview setup. Extend via
# LAI_ALLOWED_ORIGINS env var (comma-separated) if the frontend is served
# elsewhere.
DEFAULT_ALLOWED_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
}


def get_allowed_origins() -> set[str]:
    extra = os.environ.get("LAI_ALLOWED_ORIGINS", "")
    extra_set = {o.strip() for o in extra.split(",") if o.strip()}
    return DEFAULT_ALLOWED_ORIGINS | extra_set


def get_or_create_access_token(config_path: Path = DEFAULT_CONFIG_PATH) -> str:
    """Loads the access token from the local config file, generating and
    persisting a new one on first run. File permissions are locked down to
    owner-read/write only (POSIX; best-effort no-op on Windows, which
    relies on normal per-user filesystem ACLs instead)."""
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text())
            token = data.get("access_token")
            if token:
                return token
        except (json.JSONDecodeError, OSError):
            pass  # fall through and regenerate rather than crash on a corrupt file

    token = secrets.token_hex(32)
    config_path.write_text(json.dumps({"access_token": token}, indent=2))
    try:
        os.chmod(config_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass  # e.g. Windows -- not fatal, just best-effort hardening
    return token


def is_origin_allowed(origin: Optional[str], allowed: set[str]) -> bool:
    """Requests with no Origin header at all (curl, server-to-server, most
    non-browser HTTP clients) are NOT blocked here -- they still need a
    valid access token, checked separately. This check exists specifically
    to stop a malicious *webpage* in the user's browser from silently
    calling the local API using the user's own already-trusted browser
    session (the exact CORS-open-localhost class of bug referenced in the
    build prompt)."""
    if origin is None:
        return True
    return origin in allowed
