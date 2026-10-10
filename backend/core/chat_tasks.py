"""
backend/core/chat_tasks.py

Which chat turns are running right now, so deleting a chat can STOP them.

Without this a turn kept generating (and kept calling tools) in its worker
thread after the chat row was gone, until the browser happened to drop the
stream. The stream endpoint registers its cancel event here; delete_chat sets
it. Thread-safe: the event loop registers, request threads cancel.
"""

from __future__ import annotations

import threading
from typing import Optional

_lock = threading.Lock()
_running: dict[int, list[threading.Event]] = {}


def register(chat_id: int, cancel: threading.Event) -> None:
    with _lock:
        _running.setdefault(chat_id, []).append(cancel)


def unregister(chat_id: int, cancel: threading.Event) -> None:
    with _lock:
        events = _running.get(chat_id)
        if not events:
            return
        try:
            events.remove(cancel)
        except ValueError:
            pass
        if not events:
            _running.pop(chat_id, None)


def cancel_chat(chat_id: int) -> int:
    """Ask every running turn of this chat to stop. Returns how many were told."""
    with _lock:
        events = list(_running.get(chat_id, ()))
    for ev in events:
        ev.set()
    return len(events)


def running_count(chat_id: Optional[int] = None) -> int:
    with _lock:
        if chat_id is not None:
            return len(_running.get(chat_id, ()))
        return sum(len(v) for v in _running.values())
