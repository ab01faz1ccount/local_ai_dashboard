"""
tests/backend/test_events.py

Unit-level tests for core/events/: the bus's persist+publish contract,
the taxonomy helper, and the device id. No FastAPI/TestClient here --
that's test_events_api.py and the emission-wiring assertions inside
test_router.py/test_runtime_manager_events.py.
"""

import queue
import threading
import time

import pytest

from backend.core import events
from backend.core.events import bus as bus_module
from backend.core.events import device as device_module
from backend.storage import db as storage_db
from backend.storage.db import Agent, LlmAgentSession, Runtime


@pytest.fixture(autouse=True)
def _fresh_db_and_device(tmp_path, monkeypatch):
    """Every test gets its own SQLite file (storage_db.engine/SessionLocal
    rebound to point at it) and a cleared device-id cache, so tests can't
    see each other's events or device ids -- unlike most of the rest of
    this test suite, which deliberately shares one DB for the whole
    pytest session, exact-row-count assertions here need real isolation."""
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "t.sqlite3"
    fresh_engine = storage_db.make_engine(str(db_path))
    storage_db.Base.metadata.create_all(fresh_engine)
    monkeypatch.setattr(storage_db, "engine", fresh_engine)
    monkeypatch.setattr(
        storage_db, "SessionLocal", sessionmaker(bind=fresh_engine, autoflush=False, expire_on_commit=False, future=True)
    )
    device_module._reset_cache_for_tests()
    yield


def _seed_runtime_agent_session():
    """Real Runtime/Agent/LlmAgentSession rows -- `events` has honest FK
    constraints (foreign_keys=ON), so a test asserting a round-tripped
    runtime_id/agent_id/session_id needs rows that actually exist, the
    same as production would require."""
    db = storage_db.SessionLocal()
    try:
        rt = Runtime(name="rt", executable_path="/bin/true", port=8080)
        ag = Agent(name="ag")
        db.add_all([rt, ag])
        db.commit()
        db.refresh(rt)
        db.refresh(ag)
        link = storage_db.start_llm_agent_session(db, rt.id, ag.id)
        return rt.id, ag.id, link.id
    finally:
        db.close()


@pytest.fixture
def fresh_bus():
    """A brand-new EventBus instead of the process-wide singleton, so
    subscriber state from one test can't leak into another."""
    return bus_module.EventBus()


# ---------------------------------------------------------------------
# emit(): persistence
# ---------------------------------------------------------------------

def test_emit_persists_a_row_with_all_fields(fresh_bus):
    runtime_id, agent_id, session_id = _seed_runtime_agent_session()
    ev = fresh_bus.emit(
        events.EventType.RUNTIME_STARTED,
        runtime_id=runtime_id,
        agent_id=agent_id,
        session_id=session_id,
        source=events.EventSource(project_id="proj-1"),
        metadata={"endpoint": "http://127.0.0.1:8080"},
    )
    assert ev.event_type == "runtime.started"
    assert ev.device_id  # auto-filled, non-empty

    db = storage_db.SessionLocal()
    try:
        rows = storage_db.list_events(db)
        assert len(rows) == 1
        row = rows[0]
        assert row.event_id == ev.event_id
        assert row.event_type == "runtime.started"
        assert row.runtime_id == runtime_id and row.agent_id == agent_id and row.session_id == session_id
        assert row.source_system == "local" and row.source_project_id == "proj-1"
        assert row.metadata_json == {"endpoint": "http://127.0.0.1:8080"}
        assert row.device_id == ev.device_id
    finally:
        db.close()


def test_emit_accepts_plain_string_type_too(fresh_bus):
    ev = fresh_bus.emit("model.registered", metadata={"model_id": 1})
    assert ev.event_type == "model.registered"


def test_emit_defaults_are_sane(fresh_bus):
    ev = fresh_bus.emit(events.EventType.AGENT_CREATED)
    assert ev.runtime_id is None and ev.agent_id is None and ev.session_id is None
    assert ev.source.system == "local"
    assert ev.metadata == {}
    assert ev.event_id  # a real uuid4 hex, not empty
    assert len(ev.event_id) == 32


def test_each_emit_gets_a_unique_event_id(fresh_bus):
    a = fresh_bus.emit(events.EventType.RUNTIME_STARTED, runtime_id=1)
    b = fresh_bus.emit(events.EventType.RUNTIME_STARTED, runtime_id=1)
    assert a.event_id != b.event_id


# ---------------------------------------------------------------------
# emit(): live publish
# ---------------------------------------------------------------------

def test_subscribers_receive_emitted_events_in_order(fresh_bus):
    # No runtime/agent rows seeded on purpose: this only tests the
    # in-memory publish path (_persist() isolates any FK failure, tested
    # separately below), so runtime_id is deliberately left out here.
    _, q = fresh_bus.subscribe()
    fresh_bus.emit(events.EventType.RUNTIME_STARTED, metadata={"n": 1})
    fresh_bus.emit(events.EventType.RUNTIME_STOPPED, metadata={"n": 1})
    first = q.get(timeout=1)
    second = q.get(timeout=1)
    assert [first.event_type, second.event_type] == ["runtime.started", "runtime.stopped"]


def test_unsubscribed_listener_gets_nothing_further(fresh_bus):
    sub_id, q = fresh_bus.subscribe()
    fresh_bus.unsubscribe(sub_id)
    fresh_bus.emit(events.EventType.RUNTIME_STARTED)
    with pytest.raises(queue.Empty):
        q.get(timeout=0.2)


def test_multiple_subscribers_each_get_their_own_copy(fresh_bus):
    runtime_id, _, _ = _seed_runtime_agent_session()
    _, q1 = fresh_bus.subscribe()
    _, q2 = fresh_bus.subscribe()
    fresh_bus.emit(events.EventType.RUNTIME_STARTED, runtime_id=runtime_id)
    assert q1.get(timeout=1).runtime_id == runtime_id
    assert q2.get(timeout=1).runtime_id == runtime_id


def test_full_subscriber_queue_drops_oldest_not_the_emitter(fresh_bus, monkeypatch):
    monkeypatch.setattr(bus_module, "MAX_QUEUE_PER_SUBSCRIBER", 2)
    sub_id, q = fresh_bus.subscribe()
    fresh_bus._subscribers[sub_id] = queue.Queue(maxsize=2)  # rebuild with the patched cap
    q = fresh_bus._subscribers[sub_id]
    fresh_bus.emit(events.EventType.RUNTIME_STARTED, metadata={"n": 1})
    fresh_bus.emit(events.EventType.RUNTIME_STARTED, metadata={"n": 2})
    fresh_bus.emit(events.EventType.RUNTIME_STARTED, metadata={"n": 3})  # queue was full at 2 -- oldest dropped
    remaining = [q.get_nowait().metadata["n"] for _ in range(2)]
    assert remaining == [2, 3]
    assert q.empty()


def test_subscriber_count(fresh_bus):
    assert fresh_bus.subscriber_count() == 0
    sub_id, _ = fresh_bus.subscribe()
    assert fresh_bus.subscriber_count() == 1
    fresh_bus.unsubscribe(sub_id)
    assert fresh_bus.subscriber_count() == 0


def test_persistence_failure_does_not_raise_or_block_publish(fresh_bus, monkeypatch, capsys):
    """The one hard requirement: a broken event log must never break the
    real operation it's describing."""

    def boom(*a, **kw):
        raise RuntimeError("db is on fire")

    monkeypatch.setattr(storage_db, "insert_event", boom)
    _, q = fresh_bus.subscribe()
    ev = fresh_bus.emit(events.EventType.RUNTIME_CRASHED, runtime_id=1)  # must not raise
    assert q.get(timeout=1).event_id == ev.event_id  # publish still happened
    assert "db is on fire" in capsys.readouterr().err  # but it was logged, not silently eaten


def test_concurrent_emits_from_multiple_threads_all_land(fresh_bus):
    def worker(n):
        for i in range(20):
            fresh_bus.emit(events.EventType.INFERENCE_COMPLETED, metadata={"worker": n, "i": i})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    db = storage_db.SessionLocal()
    try:
        rows = storage_db.list_events(db, event_types=["inference.completed"], limit=500)
        assert len(rows) == 100
    finally:
        db.close()


# ---------------------------------------------------------------------
# module-level `events.emit` uses the process-wide singleton
# ---------------------------------------------------------------------

def test_module_level_emit_uses_the_singleton_bus():
    runtime_id, agent_id, session_id = _seed_runtime_agent_session()
    events.emit(events.EventType.SESSION_STARTED, runtime_id=runtime_id, agent_id=agent_id, session_id=session_id)
    db = storage_db.SessionLocal()
    try:
        rows = storage_db.list_events(db, event_types=["session.started"])
        assert any(r.runtime_id == runtime_id and r.agent_id == agent_id and r.session_id == session_id for r in rows)
    finally:
        db.close()


# ---------------------------------------------------------------------
# taxonomy
# ---------------------------------------------------------------------

def test_describe_all_covers_every_enum_member_exactly_once():
    described = events.describe_all()
    types = [d["event_type"] for d in described]
    assert len(types) == len(set(types)) == len(list(events.EventType))
    assert set(types) == {t.value for t in events.EventType}
    assert all(d["description"] for d in described)  # nothing blank


def test_describe_all_is_sorted():
    described = events.describe_all()
    types = [d["event_type"] for d in described]
    assert types == sorted(types)


# ---------------------------------------------------------------------
# device id
# ---------------------------------------------------------------------

def test_device_id_is_created_once_and_persisted():
    device_module._reset_cache_for_tests()
    first = device_module.get_device_id()
    device_module._reset_cache_for_tests()  # force a re-read from the DB, not the in-memory cache
    second = device_module.get_device_id()
    assert first == second and len(first) == 32


def test_device_id_is_cached_after_first_call(monkeypatch):
    device_module._reset_cache_for_tests()
    calls = {"n": 0}
    real_session_local = storage_db.SessionLocal

    def counting_session_local():
        calls["n"] += 1
        return real_session_local()

    monkeypatch.setattr(storage_db, "SessionLocal", counting_session_local)
    device_module.get_device_id()
    device_module.get_device_id()
    device_module.get_device_id()
    assert calls["n"] == 1  # only the first call touched the DB
