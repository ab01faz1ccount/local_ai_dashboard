"""
tests/backend/test_runtime_manager_events.py

Verifies runtime_manager.py's event emission: exactly what fires for
each outcome of start()/stop()/restart(), and the poll-based crash/
stopped-detection path in get_status() (what the /ws/metrics loop
relies on to notice a process died between polls).

A FakeEngine stands in for LlamaCppEngine so each test can dictate
exactly what status sequence start()/stop()/restart()/get_status()
return, without touching a real process.
"""

import pytest

from backend.core import events
from backend.core.engine.base import EngineMetrics, EngineStatus, RuntimeState
from backend.core.runtime_manager import RuntimeManager
from backend.storage import db as storage_db
from backend.storage.db import MLModel, Runtime


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    fresh_engine = storage_db.make_engine(str(tmp_path / "t.sqlite3"))
    storage_db.Base.metadata.create_all(fresh_engine)
    monkeypatch.setattr(storage_db, "engine", fresh_engine)
    monkeypatch.setattr(
        storage_db, "SessionLocal", sessionmaker(bind=fresh_engine, autoflush=False, expire_on_commit=False, future=True)
    )
    from backend.core.events import device as device_module

    device_module._reset_cache_for_tests()
    yield


class FakeEngine:
    """however many calls the test needs, each returning a pre-scripted
    EngineStatus in order."""

    def __init__(self, *statuses: EngineStatus):
        self._statuses = list(statuses)
        self.calls: list[str] = []

    def _next(self, call: str) -> EngineStatus:
        self.calls.append(call)
        return self._statuses.pop(0) if self._statuses else EngineStatus(state=RuntimeState.OFFLINE)

    def start(self, config: dict) -> EngineStatus:
        return self._next("start")

    def stop(self, timeout: float = 10.0) -> EngineStatus:
        return self._next("stop")

    def restart(self, config: dict) -> EngineStatus:
        return self._next("restart")

    def get_status(self) -> EngineStatus:
        return self._next("get_status")

    def get_metrics(self) -> EngineMetrics:
        return EngineMetrics()

    def tail_logs(self, n: int = 200) -> list[str]:
        return []


def _make_runtime(db, *, model_id=None, status="OFFLINE") -> Runtime:
    rt = Runtime(name="rt1", executable_path="/bin/true", port=8080, model_id=model_id, status=status)
    db.add(rt)
    db.commit()
    db.refresh(rt)
    return rt


def _events_for(db, runtime_id=None):
    rows = storage_db.list_events(db, runtime_id=runtime_id, limit=500)
    return [(r.event_type, r.metadata_json) for r in reversed(rows)]  # chronological


@pytest.fixture
def db():
    session = storage_db.SessionLocal()
    yield session
    session.close()


@pytest.fixture
def manager():
    return RuntimeManager(platform_provider=object())  # never touched by these tests


# ---------------------------------------------------------------------
# start()
# ---------------------------------------------------------------------

def test_start_success_emits_runtime_started(db, manager):
    rt = _make_runtime(db)
    manager._engines[rt.id] = FakeEngine(EngineStatus(state=RuntimeState.ONLINE, endpoint="http://127.0.0.1:8080"))
    manager.start(db, rt)
    types = [t for t, _ in _events_for(db, rt.id)]
    assert types == ["runtime.started"]


def test_start_with_model_id_also_emits_model_loaded(db, manager):
    model = MLModel(name="m", file_path="/m.gguf", file_size_bytes=1)
    db.add(model)
    db.commit()
    db.refresh(model)
    rt = _make_runtime(db, model_id=model.id)
    manager._engines[rt.id] = FakeEngine(EngineStatus(state=RuntimeState.ONLINE))
    manager.start(db, rt)
    types = [t for t, _ in _events_for(db, rt.id)]
    assert types == ["runtime.started", "model.loaded"]
    meta = dict(_events_for(db, rt.id))["model.loaded"]
    assert meta["model_id"] == model.id


def test_start_without_model_id_does_not_emit_model_loaded(db, manager):
    rt = _make_runtime(db, model_id=None)
    manager._engines[rt.id] = FakeEngine(EngineStatus(state=RuntimeState.ONLINE))
    manager.start(db, rt)
    types = [t for t, _ in _events_for(db, rt.id)]
    assert "model.loaded" not in types


def test_start_failure_emits_runtime_crashed_not_started(db, manager):
    rt = _make_runtime(db)
    manager._engines[rt.id] = FakeEngine(EngineStatus(state=RuntimeState.ERROR, error_message="boom"))
    manager.start(db, rt)
    ev = _events_for(db, rt.id)
    assert [t for t, _ in ev] == ["runtime.crashed"]
    assert ev[0][1]["phase"] == "start" and ev[0][1]["error"] == "boom"


# ---------------------------------------------------------------------
# stop()
# ---------------------------------------------------------------------

def test_stop_success_emits_runtime_stopped(db, manager):
    model = MLModel(name="m", file_path="/m.gguf", file_size_bytes=1)
    db.add(model)
    db.commit()
    db.refresh(model)
    rt = _make_runtime(db, model_id=model.id, status="ONLINE")
    manager._engines[rt.id] = FakeEngine(EngineStatus(state=RuntimeState.OFFLINE))
    manager.stop(db, rt)
    types = [t for t, _ in _events_for(db, rt.id)]
    assert types == ["runtime.stopped", "model.unloaded"]


def test_stop_failure_emits_runtime_crashed(db, manager):
    rt = _make_runtime(db, status="ONLINE")
    manager._engines[rt.id] = FakeEngine(EngineStatus(state=RuntimeState.ERROR, error_message="wedged"))
    manager.stop(db, rt)
    ev = _events_for(db, rt.id)
    assert [t for t, _ in ev] == ["runtime.crashed"]
    assert ev[0][1]["phase"] == "stop"


# ---------------------------------------------------------------------
# restart()
# ---------------------------------------------------------------------

def test_restart_success_emits_stopped_then_started(db, manager):
    rt = _make_runtime(db, status="ONLINE")
    manager._engines[rt.id] = FakeEngine(EngineStatus(state=RuntimeState.ONLINE, endpoint="http://x"))
    manager.restart(db, rt)
    types = [t for t, _ in _events_for(db, rt.id)]
    assert types == ["runtime.stopped", "runtime.started"]


def test_restart_failure_emits_only_crashed(db, manager):
    rt = _make_runtime(db, status="ONLINE")
    manager._engines[rt.id] = FakeEngine(EngineStatus(state=RuntimeState.ERROR, error_message="nope"))
    manager.restart(db, rt)
    types = [t for t, _ in _events_for(db, rt.id)]
    assert types == ["runtime.crashed"]


# ---------------------------------------------------------------------
# get_status() -- the poll-based crash/stopped detector
# ---------------------------------------------------------------------

def test_poll_detects_crash_only_on_online_to_error_transition(db, manager):
    rt = _make_runtime(db, status="ONLINE")
    manager._engines[rt.id] = FakeEngine(EngineStatus(state=RuntimeState.ERROR, error_message="died"))
    manager.get_status(db, rt)
    types = [t for t, _ in _events_for(db, rt.id)]
    assert types == ["runtime.crashed"]
    assert dict(_events_for(db, rt.id))["runtime.crashed"]["phase"] == "poll"


def test_poll_detects_unexpected_stop(db, manager):
    rt = _make_runtime(db, status="ONLINE")
    manager._engines[rt.id] = FakeEngine(EngineStatus(state=RuntimeState.OFFLINE))
    manager.get_status(db, rt)
    types = [t for t, _ in _events_for(db, rt.id)]
    assert types == ["runtime.stopped"]
    assert dict(_events_for(db, rt.id))["runtime.stopped"]["detected_via"] == "poll"


def test_poll_with_no_state_change_emits_nothing(db, manager):
    rt = _make_runtime(db, status="ONLINE")
    manager._engines[rt.id] = FakeEngine(EngineStatus(state=RuntimeState.ONLINE))
    manager.get_status(db, rt)
    assert _events_for(db, rt.id) == []


def test_poll_from_offline_to_offline_emits_nothing(db, manager):
    rt = _make_runtime(db, status="OFFLINE")
    manager._engines[rt.id] = FakeEngine(EngineStatus(state=RuntimeState.OFFLINE))
    manager.get_status(db, rt)
    assert _events_for(db, rt.id) == []


def test_explicit_start_does_not_double_fire_when_polled_right_after(db, manager):
    """start() already emitted runtime.started; get_status() immediately
    after must not also see ONLINE-had-been-OFFLINE and fire again --
    _sync_db() has already written the new state to the DB row by the
    time get_status() looks, so its "previous_state" snapshot is the
    post-start ONLINE, not the pre-start OFFLINE."""
    rt = _make_runtime(db, status="OFFLINE")
    manager._engines[rt.id] = FakeEngine(
        EngineStatus(state=RuntimeState.ONLINE), EngineStatus(state=RuntimeState.ONLINE)
    )
    manager.start(db, rt)
    manager.get_status(db, rt)
    types = [t for t, _ in _events_for(db, rt.id)]
    assert types == ["runtime.started"]  # not ["runtime.started", "runtime.started"] or a crash
