"""
tests/backend/test_metrics_api.py

GET /api/v1/runtimes/{id}/metrics/history -- the read side of
core/metrics.py's background sampler, through the full app via
TestClient.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.core.security import get_or_create_access_token
from backend.storage import db as storage_db
from backend.storage.db import MetricsSnapshot, Runtime

AUTH = {"Authorization": f"Bearer {get_or_create_access_token()}"}


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
def client(monkeypatch):
    from backend.core.metrics import metrics_recorder

    monkeypatch.setattr(metrics_recorder, "start", lambda: None)  # don't start the real background thread in tests
    monkeypatch.setattr(metrics_recorder, "stop", lambda: None)
    from backend.main import app

    with TestClient(app) as c:
        yield c


def get(c, path, **kw):
    return c.get(f"/api/v1{path}", headers=AUTH, **kw)


def make_runtime() -> int:
    with storage_db.SessionLocal() as s:
        rt = Runtime(name="r", executable_path="/bin/true", host="127.0.0.1", port=1)
        s.add(rt)
        s.commit()
        return rt.id


def add_snapshot(runtime_id, **over):
    defaults = dict(cpu_percent=10.0, ram_used_mb=100.0, ram_total_mb=1000.0, tokens_per_sec=5.0, last_latency_ms=50.0)
    defaults.update(over)
    with storage_db.SessionLocal() as s:
        snap = MetricsSnapshot(runtime_id=runtime_id, **defaults)
        s.add(snap)
        s.commit()
        return snap.id


def test_requires_token(client):
    rid = make_runtime()
    assert client.get(f"/api/v1/runtimes/{rid}/metrics/history").status_code == 401


def test_unknown_runtime_is_404(client):
    assert get(client, "/runtimes/999999/metrics/history").status_code == 404


def test_empty_history_is_an_empty_list(client):
    rid = make_runtime()
    assert get(client, f"/runtimes/{rid}/metrics/history").json() == []


def test_history_is_newest_first_with_full_fields(client):
    rid = make_runtime()
    add_snapshot(rid, tokens_per_sec=1.0)
    add_snapshot(rid, tokens_per_sec=2.0)
    rows = get(client, f"/runtimes/{rid}/metrics/history").json()
    assert [r["tokens_per_sec"] for r in rows] == [2.0, 1.0]
    assert set(rows[0]) == {
        "id", "runtime_id", "timestamp", "cpu_percent", "ram_used_mb", "ram_total_mb",
        "gpu_percent", "vram_used_mb", "vram_total_mb", "tokens_per_sec", "last_latency_ms",
    }


def test_history_is_scoped_to_the_runtime(client):
    r1, r2 = make_runtime(), make_runtime()
    add_snapshot(r1)
    add_snapshot(r2)
    assert len(get(client, f"/runtimes/{r1}/metrics/history").json()) == 1


def test_limit_is_respected(client):
    rid = make_runtime()
    for i in range(5):
        add_snapshot(rid, tokens_per_sec=float(i))
    assert len(get(client, f"/runtimes/{rid}/metrics/history?limit=2").json()) == 2


def test_limit_over_the_cap_is_422(client):
    rid = make_runtime()
    assert get(client, f"/runtimes/{rid}/metrics/history?limit=99999").status_code == 422


def test_since_filters_by_timestamp(client):
    rid = make_runtime()
    add_snapshot(rid, timestamp="2020-01-01T00:00:00Z", tokens_per_sec=1.0)
    add_snapshot(rid, timestamp="2026-01-01T00:00:00Z", tokens_per_sec=2.0)
    rows = get(client, f"/runtimes/{rid}/metrics/history?since=2025-01-01T00:00:00Z").json()
    assert [r["tokens_per_sec"] for r in rows] == [2.0]
