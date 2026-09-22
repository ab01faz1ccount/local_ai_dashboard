"""
backend/core/agents/paths.py

Remembers where the user told us an agent CLI lives (picked through the
offline browser), for the case where it isn't on PATH.

Deliberately a small JSON file next to the SQLite DB instead of a DB
table: adapters are process-wide singletons that resolve their
executable lazily on every call (`shutil.which` today), so a plain
`get_path(backend_id)` they can call from anywhere -- with no DB session
to thread through and no startup hook to wire -- keeps that contract
intact.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

from ...storage.db import DEFAULT_DB_PATH

_LOCK = threading.Lock()


def _store_path() -> Path:
    return Path(DEFAULT_DB_PATH).resolve().parent / "agent_paths.json"


def _load() -> dict[str, str]:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get_path(backend_id: str) -> Optional[str]:
    """The user-picked executable for this backend, if it still exists."""
    with _LOCK:
        p = _load().get(backend_id)
    if p and os.path.isfile(p):
        return p
    return None


def set_path(backend_id: str, path: str) -> None:
    with _LOCK:
        data = _load()
        data[backend_id] = path
        tmp = _store_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, _store_path())


def clear_path(backend_id: str) -> None:
    with _LOCK:
        data = _load()
        if data.pop(backend_id, None) is not None:
            tmp = _store_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, _store_path())
