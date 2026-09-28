"""
backend/core/events/device.py

A stable identifier for "this machine", persisted once in `app_settings`
so it survives restarts. This is NOT a full Device entity -- schema.sql
has no `devices` table yet, since nothing in this app currently needs to
distinguish between multiple physical machines (that's future work, once
Synapse can actually route across more than one device). It's just
enough that every event this device emits carries a consistent
`device_id`, per the event shape in the master build prompt (section 16).

Cached in memory after the first read/write so `get_device_id()` is
cheap to call on every single event emission.
"""

from __future__ import annotations

import threading
import uuid
from typing import Optional

from ...storage import db as storage_db

_DEVICE_ID_KEY = "device_id"
_lock = threading.Lock()
_cached: Optional[str] = None


def get_device_id() -> str:
    global _cached
    if _cached is not None:
        return _cached
    with _lock:
        if _cached is not None:  # another thread won the race while we waited
            return _cached
        # storage_db.SessionLocal() (module-qualified), not a name bound
        # once at import time -- so this function always uses whatever
        # engine storage_db is CURRENTLY pointed at. Tests rebind
        # storage_db.SessionLocal to an isolated per-test engine; a
        # load-time `from ...storage.db import SessionLocal` would have
        # frozen this function onto the very first engine it ever saw.
        db = storage_db.SessionLocal()
        try:
            row = db.get(storage_db.AppSetting, _DEVICE_ID_KEY)
            if row is None:
                new_id = uuid.uuid4().hex
                db.add(storage_db.AppSetting(key=_DEVICE_ID_KEY, value=new_id))
                db.commit()
                _cached = new_id
            else:
                _cached = row.value
        finally:
            db.close()
    return _cached


def _reset_cache_for_tests() -> None:
    """Test-only: clears the in-process cache so a test using a fresh
    in-memory DB doesn't see a previous test's cached id."""
    global _cached
    with _lock:
        _cached = None
