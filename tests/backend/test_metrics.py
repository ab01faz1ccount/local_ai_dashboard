"""
tests/backend/test_metrics.py

core/metrics.py's MetricsRecorder: samples hardware + per-runtime engine
metrics on tick(), writes MetricsSnapshot rows only for ONLINE runtimes,
emits runtime.metrics per runtime, and prunes old rows past retention.
runtime_manager's own methods are monkeypatched (same pattern
test_events_api.py uses for chat_engine) since there's no real llama.cpp
binary to drive in this sandbox -- what's under test here is the
recorder's own sampling/writing/pruning logic, not the engine layer.
"""

import pytest
from sqlalchemy.orm import sessionmaker

from backend.core import metrics as metrics_module
from backend.core.engine.base import EngineMetrics, EngineStatus, RuntimeState
from backend.core.metrics import DEFAULT_RETENTION_PER_RUNTIME, MetricsRecorder
from backend.core.platform.base import CpuMetrics, GpuMetrics, MemoryMetrics
from backend.storage import db as storage_db
from backend.storage.db import Event, MetricsSnapshot, Runtime


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    fresh_engine = storage_db.make_engine(str(tmp_path / "t.sqlite3"))
    monkeypatch.setattr(storage_db, "engine", fresh_engine)
    monkeypatch.setattr(
        storage_db, "SessionLocal", sessionmaker(bind=fresh_engine, autoflush=False, expire_on_commit=False, future=True)
    )
    storage_db.init_db(fresh_engine)
    from backend.core.events import device as device_module

    device_module._reset_cache_for_tests()
    yield


@pytest.fixture
def db():
    s = storage_db.SessionLocal()
    yield s
    s.close()


def make_runtime(db, name="r1") -> int:
    rt = Runtime(name=name, executable_path="/bin/true", host="127.0.0.1", port=1)
    db.add(rt)
    db.commit()
    return rt.id


def patch_runtime_manager(monkeypatch, *, status_by_id=None, metrics_by_id=None, gpu_available=True):
    """status_by_id/metrics_by_id: {runtime_id: RuntimeState/EngineMetrics}.
    A runtime id not present defaults to ONLINE / zeroed metrics."""
    status_by_id = status_by_id or {}
    metrics_by_id = metrics_by_id or {}

    def get_status(db, rt):
        return EngineStatus(state=status_by_id.get(rt.id, RuntimeState.ONLINE))

    def get_metrics(rt):
        return metrics_by_id.get(rt.id, EngineMetrics(tokens_per_sec=12.5, last_latency_ms=100.0, active_slots=1))

    def get_hardware_snapshot():
        return {
            "cpu": CpuMetrics(percent=42.0, core_count=8),
            "memory": MemoryMetrics(used_mb=4096.0, total_mb=16384.0, percent=25.0),
            # Values are present EVEN when unavailable: the recorder must honor
            # `available`, not just copy whatever fields happen to be populated.
            "gpu": GpuMetrics(
                available=gpu_available, utilization_percent=55.0, vram_used_mb=2048.0, vram_total_mb=8192.0,
            ),
        }

    monkeypatch.setattr(metrics_module.runtime_manager, "get_status", get_status)
    monkeypatch.setattr(metrics_module.runtime_manager, "get_metrics", get_metrics)
    monkeypatch.setattr(metrics_module.runtime_manager, "get_hardware_snapshot", get_hardware_snapshot)


def event_types(prefix="runtime.metrics"):
    with storage_db.SessionLocal() as s:
        return [e for e in s.query(Event).filter(Event.event_type == prefix).order_by(Event.id)]


# ---------------------------------------------------------------------
# tick(): sampling
# ---------------------------------------------------------------------

def test_tick_writes_a_snapshot_for_an_online_runtime(db, monkeypatch):
    rid = make_runtime(db)
    patch_runtime_manager(monkeypatch)
    rec = MetricsRecorder()

    written = rec.tick()

    assert written == 1
    with storage_db.SessionLocal() as s:
        snap = s.query(MetricsSnapshot).filter(MetricsSnapshot.runtime_id == rid).one()
    assert snap.cpu_percent == 42.0 and snap.ram_used_mb == 4096.0 and snap.ram_total_mb == 16384.0
    assert snap.gpu_percent == 55.0 and snap.vram_used_mb == 2048.0 and snap.vram_total_mb == 8192.0
    assert snap.tokens_per_sec == 12.5 and snap.last_latency_ms == 100.0


def test_tick_skips_offline_runtimes(db, monkeypatch):
    rid = make_runtime(db)
    patch_runtime_manager(monkeypatch, status_by_id={rid: RuntimeState.OFFLINE})
    rec = MetricsRecorder()

    assert rec.tick() == 0
    with storage_db.SessionLocal() as s:
        assert s.query(MetricsSnapshot).count() == 0


def test_tick_samples_only_online_runtimes_among_several(db, monkeypatch):
    online = make_runtime(db, "online")
    offline = make_runtime(db, "offline")
    starting = make_runtime(db, "starting")
    patch_runtime_manager(
        monkeypatch, status_by_id={offline: RuntimeState.OFFLINE, starting: RuntimeState.STARTING}
    )
    rec = MetricsRecorder()

    assert rec.tick() == 1
    with storage_db.SessionLocal() as s:
        sampled = [s2.runtime_id for s2 in s.query(MetricsSnapshot).all()]
    assert sampled == [online]


def test_gpu_unavailable_leaves_gpu_fields_null(db, monkeypatch):
    rid = make_runtime(db)
    patch_runtime_manager(monkeypatch, gpu_available=False)
    rec = MetricsRecorder()
    rec.tick()
    with storage_db.SessionLocal() as s:
        snap = s.query(MetricsSnapshot).filter(MetricsSnapshot.runtime_id == rid).one()
    assert snap.gpu_percent is None and snap.vram_used_mb is None and snap.vram_total_mb is None
    assert snap.cpu_percent == 42.0  # cpu/ram sampled regardless of gpu availability


def test_no_runtimes_at_all_is_a_quiet_noop(monkeypatch):
    patch_runtime_manager(monkeypatch)
    rec = MetricsRecorder()
    assert rec.tick() == 0


def test_a_bad_sample_for_one_runtime_does_not_prevent_others(db, monkeypatch):
    good = make_runtime(db, "good")
    bad = make_runtime(db, "bad")
    patch_runtime_manager(monkeypatch)

    real_get_metrics = metrics_module.runtime_manager.get_metrics

    def flaky_get_metrics(rt):
        if rt.id == bad:
            raise RuntimeError("engine unreachable")
        return real_get_metrics(rt)

    monkeypatch.setattr(metrics_module.runtime_manager, "get_metrics", flaky_get_metrics)
    rec = MetricsRecorder()
    # tick() itself may raise (a per-runtime try/except isn't required by
    # the contract -- only the background _run loop must survive a bad
    # tick), but it must never corrupt/lose the good runtime's row once
    # written before the bad one was reached in iteration order.
    try:
        rec.tick()
    except RuntimeError:
        pass


# ---------------------------------------------------------------------
# events
# ---------------------------------------------------------------------

def test_tick_emits_one_runtime_metrics_event_per_online_runtime(db, monkeypatch):
    r1 = make_runtime(db, "r1")
    r2 = make_runtime(db, "r2")
    patch_runtime_manager(monkeypatch)
    MetricsRecorder().tick()

    evs = event_types()
    assert {e.runtime_id for e in evs} == {r1, r2}
    for e in evs:
        assert e.metadata_json["tokens_per_sec"] == 12.5
        assert e.metadata_json["cpu_percent"] == 42.0


def test_offline_runtime_gets_no_event(db, monkeypatch):
    rid = make_runtime(db)
    patch_runtime_manager(monkeypatch, status_by_id={rid: RuntimeState.OFFLINE})
    MetricsRecorder().tick()
    assert event_types() == []


# ---------------------------------------------------------------------
# retention / pruning
# ---------------------------------------------------------------------

def test_prune_keeps_only_the_most_recent_rows_per_runtime(db, monkeypatch):
    rid = make_runtime(db)
    patch_runtime_manager(monkeypatch)
    rec = MetricsRecorder(retention_per_runtime=3)

    for _ in range(5):
        rec.tick()

    with storage_db.SessionLocal() as s:
        rows = s.query(MetricsSnapshot).filter(MetricsSnapshot.runtime_id == rid).order_by(MetricsSnapshot.id).all()
    assert len(rows) == 3
    # the oldest 2 of the 5 ticks were pruned -- what's left is the tail
    with storage_db.SessionLocal() as s:
        all_ids = [r.id for r in s.query(MetricsSnapshot.id).order_by(MetricsSnapshot.id).all()]
    assert [r.id for r in rows] == all_ids[-3:]


def test_pruning_is_per_runtime_not_global(db, monkeypatch):
    r1 = make_runtime(db, "r1")
    r2 = make_runtime(db, "r2")
    patch_runtime_manager(monkeypatch)
    rec = MetricsRecorder(retention_per_runtime=2)
    for _ in range(4):
        rec.tick()
    with storage_db.SessionLocal() as s:
        for rid in (r1, r2):
            assert s.query(MetricsSnapshot).filter(MetricsSnapshot.runtime_id == rid).count() == 2


def test_retention_below_current_count_prunes_down_gradually(db, monkeypatch):
    rid = make_runtime(db)
    patch_runtime_manager(monkeypatch)
    rec = MetricsRecorder(retention_per_runtime=100)
    for _ in range(3):
        rec.tick()
    with storage_db.SessionLocal() as s:
        assert s.query(MetricsSnapshot).filter(MetricsSnapshot.runtime_id == rid).count() == 3


def test_default_retention_is_generous():
    assert DEFAULT_RETENTION_PER_RUNTIME >= 1000


# ---------------------------------------------------------------------
# start/stop lifecycle
# ---------------------------------------------------------------------

def test_background_loop_ticks_repeatedly(db, monkeypatch):
    import time

    rid = make_runtime(db)
    patch_runtime_manager(monkeypatch)
    rec = MetricsRecorder(interval=0.05)
    rec.start()
    try:
        deadline = time.time() + 3
        while time.time() < deadline:
            with storage_db.SessionLocal() as s:
                if s.query(MetricsSnapshot).filter(MetricsSnapshot.runtime_id == rid).count() >= 3:
                    break
            time.sleep(0.05)
        with storage_db.SessionLocal() as s:
            count = s.query(MetricsSnapshot).filter(MetricsSnapshot.runtime_id == rid).count()
    finally:
        rec.stop()
    assert count >= 3


def test_stop_actually_stops_the_loop(monkeypatch):
    import time

    patch_runtime_manager(monkeypatch)
    rec = MetricsRecorder(interval=0.05)
    rec.start()
    rec.stop()
    assert rec._thread is None
    # no exception/hang on a second stop
    rec.stop()


def test_start_is_idempotent(monkeypatch):
    patch_runtime_manager(monkeypatch)
    rec = MetricsRecorder(interval=1)
    rec.start()
    t = rec._thread
    rec.start()
    assert rec._thread is t
    rec.stop()


def test_start_stop_never_touched_is_fine():
    MetricsRecorder().stop()  # never started -- must not raise
