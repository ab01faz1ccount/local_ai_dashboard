"""
backend/core/events/bus.py

The structured event system (master build prompt section 16): the ONE
place in the codebase that writes to the `events` table or fans an event
out to live subscribers. Nothing else should touch `storage_db.insert_event`
directly, and nothing else should write to `Event` rows -- same discipline
as runtime_manager.py being the one place that touches an InferenceEngine.

Two jobs, one call (`events.emit(...)`):
  1. Persistence -- append-only insert into `events`, so a Logs page or
     analytics query can see history even after every live subscriber has
     disconnected.
  2. Live push -- an in-memory pub/sub fan-out to whatever is currently
     listening (today: GET /ws/events; later: a Notifications panel, or a
     Synapse `stream_session_events()` bridge).

Emission NEVER raises and NEVER blocks the caller on a slow subscriber: a
stalled Logs page must not stop a runtime from starting, and a full
subscriber queue drops its oldest entry rather than back-pressuring the
emitter. This mirrors the isolation discipline already used elsewhere in
this codebase (an engine crash doesn't take down the app; a failed
verification doesn't corrupt a model row) -- an event system that can
break the very operation it's describing has failed at its one job.
"""

from __future__ import annotations

import queue
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional, Union

from .device import get_device_id
from .taxonomy import EventType

MAX_QUEUE_PER_SUBSCRIBER = 500  # drop-oldest cap so one stalled listener can't grow without bound


@dataclass
class EventSource:
    """Mirrors the `source` object in the master prompt's event JSON
    (section 15/16): who/what this event is attributed to. Defaults to
    this device acting locally -- the only case that exists today, since
    Synapse integration (core/integrations/synapse/) is a contract stub
    with no live orchestration yet. A future Synapse bridge would pass a
    populated EventSource when acting on Synapse's behalf."""

    system: str = "local"
    project_id: Optional[str] = None
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    task_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Event:
    event_id: str
    event_type: str
    timestamp: str
    device_id: Optional[str] = None
    runtime_id: Optional[int] = None
    agent_id: Optional[int] = None
    session_id: Optional[int] = None
    source: EventSource = field(default_factory=EventSource)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "device_id": self.device_id,
            "runtime_id": self.runtime_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "source": self.source.to_dict(),
            "metadata": self.metadata,
        }


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[int, "queue.Queue[Event]"] = {}
        self._next_subscriber_id = 0
        self._lock = threading.Lock()

    def emit(
        self,
        event_type: Union[EventType, str],
        *,
        runtime_id: Optional[int] = None,
        agent_id: Optional[int] = None,
        session_id: Optional[int] = None,
        source: Optional[EventSource] = None,
        metadata: Optional[dict] = None,
    ) -> Event:
        # Local import: storage.db imports nothing from core.events, so
        # this isn't a real cycle, but importing at call time (rather than
        # module load time) keeps this module safe to import from
        # anywhere in `core` without caring about import order at
        # startup.
        from ...storage.db import utcnow_iso

        ev = Event(
            event_id=uuid.uuid4().hex,
            event_type=event_type.value if isinstance(event_type, EventType) else str(event_type),
            timestamp=utcnow_iso(),
            device_id=get_device_id(),
            runtime_id=runtime_id,
            agent_id=agent_id,
            session_id=session_id,
            source=source or EventSource(),
            metadata=metadata or {},
        )
        self._persist(ev)
        self._publish(ev)
        return ev

    def _persist(self, ev: Event) -> None:
        from ...storage import db as storage_db

        try:
            db = storage_db.SessionLocal()
            try:
                storage_db.insert_event(
                    db,
                    event_id=ev.event_id,
                    event_type=ev.event_type,
                    timestamp=ev.timestamp,
                    device_id=ev.device_id,
                    runtime_id=ev.runtime_id,
                    agent_id=ev.agent_id,
                    session_id=ev.session_id,
                    source_system=ev.source.system,
                    source_project_id=ev.source.project_id,
                    source_agent_id=ev.source.agent_id,
                    source_session_id=ev.source.session_id,
                    source_task_id=ev.source.task_id,
                    metadata_json=ev.metadata,
                )
            finally:
                db.close()
        except Exception as exc:  # a broken event log must never break the caller's real action
            print(f"[events] failed to persist {ev.event_type}: {exc}", file=sys.stderr)

    def _publish(self, ev: Event) -> None:
        with self._lock:
            subscribers = list(self._subscribers.values())
        for q in subscribers:
            try:
                q.put_nowait(ev)
            except queue.Full:
                try:
                    q.get_nowait()  # drop the oldest to make room, keep the live stream moving
                    q.put_nowait(ev)
                except queue.Empty:
                    pass

    def subscribe(self) -> tuple[int, "queue.Queue[Event]"]:
        """Registers a new live listener. Returns (subscriber_id, queue);
        the caller reads from the queue (typically in a background
        thread -- see api/ws.py's events_ws) and MUST call unsubscribe()
        when done, or the queue leaks for the life of the process."""
        q: "queue.Queue[Event]" = queue.Queue(maxsize=MAX_QUEUE_PER_SUBSCRIBER)
        with self._lock:
            sub_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[sub_id] = q
        return sub_id, q

    def unsubscribe(self, sub_id: int) -> None:
        with self._lock:
            self._subscribers.pop(sub_id, None)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


# Single process-wide instance, same reasoning as runtime_manager's
# module-level singleton: there's only one control-center process, so a
# module-level bus (imported wherever `events.emit(...)` is called) is
# simpler than threading an instance through FastAPI's dependency system
# for something this stateful.
event_bus = EventBus()


def emit(event_type: Union[EventType, str], **kwargs) -> Event:
    """Module-level convenience: `from ..core import events` then
    `events.emit(EventType.RUNTIME_STARTED, runtime_id=rt.id)`, instead of
    importing the singleton bus directly everywhere."""
    return event_bus.emit(event_type, **kwargs)
