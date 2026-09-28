"""
backend/core/events/__init__.py

Public surface of the event system. Everywhere else in the codebase
should import from here, not from `.bus`/`.types` directly:

    from ..core import events
    events.emit(events.EventType.RUNTIME_STARTED, runtime_id=rt.id)
"""

from .bus import Event, EventSource, emit, event_bus
from .taxonomy import EventType, describe_all

__all__ = ["Event", "EventSource", "EventType", "emit", "event_bus", "describe_all"]
