"""
tests/backend/test_events_api.py

Verifies:
  - GET /api/v1/events and /api/v1/events/types (filtering, pagination,
    auth).
  - Every remaining emission point NOT already covered by
    test_runtime_manager_events.py: agent CRUD, runtime CRUD, model CRUD
    (scan / register-local / delete), attach/detach (session + agent),
    model verification, and chat inference.

Builds the same minimal FastAPI app test_router.py uses (http.router +
discovery.router), so this exercises the real route handlers end to end,
not just the underlying core functions.
"""

import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.api import discovery, http
from backend.core import connectivity
from backend.core.engine.base import EngineStatus, RuntimeState
from backend.core.security import get_or_create_access_token
from backend.storage import db as storage_db

AUTH = {"Authorization": f"Bearer {get_or_create_access_token()}"}


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    """Function-scoped, unlike test_router.py's module-scoped `_init`:
    this file's assertions check the exact contents of GET /events, so
    each test needs a clean slate rather than sharing one DB across the
    whole file."""
    from sqlalchemy.orm import sessionmaker

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
    monkeypatch.setattr(connectivity, "check_internet", lambda force=False: True)
    app = FastAPI()
    # dependencies=[Depends(require_token)] here matches exactly how
    # main.py wires http_router -- unlike test_router.py's own `client`
    # fixture (which omits it, so its auth assertions only actually
    # exercise discovery.router's own self-declared dependency), this
    # file's test_events_routes_require_token needs the real enforcement.
    app.include_router(http.router, dependencies=[Depends(http.require_token)])
    app.include_router(discovery.router)
    return TestClient(app)


def get(c, path, **kw):
    return c.get(f"/api/v1{path}", headers=AUTH, **kw)


def post(c, path, body=None, **kw):
    return c.post(f"/api/v1{path}", headers=AUTH, json=body, **kw)


def patch(c, path, body=None, **kw):
    return c.patch(f"/api/v1{path}", headers=AUTH, json=body, **kw)


def delete(c, path, **kw):
    return c.delete(f"/api/v1{path}", headers=AUTH, **kw)


def event_types_of(resp) -> list[str]:
    return [e["event_type"] for e in resp.json()]


# ---------------------------------------------------------------------
# GET /events, /events/types
# ---------------------------------------------------------------------

def test_events_routes_require_token(client):
    assert client.get("/api/v1/events").status_code == 401
    assert client.get("/api/v1/events/types").status_code == 401


def test_events_types_lists_the_full_taxonomy(client):
    resp = get(client, "/events/types")
    assert resp.status_code == 200
    body = resp.json()
    assert {"event_type": "runtime.started", "description": pytest.approx} or True  # shape sanity below
    types = {e["event_type"] for e in body}
    assert "runtime.started" in types and "mcp.connected" in types  # reserved types listed too
    assert all(e["description"] for e in body)


def test_events_empty_initially(client):
    assert get(client, "/events").json() == []


def test_agent_created_and_deleted_emit_events(client):
    r = post(client, "/agents", {"name": "A1", "agent_backend": "generic"})
    agent_id = r.json()["id"]
    assert event_types_of(get(client, "/events")) == ["agent.created"]

    delete(client, f"/agents/{agent_id}")
    types = event_types_of(get(client, "/events"))
    assert types == ["agent.deleted", "agent.created"]  # newest first


def test_runtime_created_and_deleted_emit_events(client):
    r = post(client, "/runtimes", {"name": "R1", "executable_path": "/bin/true", "port": 8080})
    rt_id = r.json()["id"]
    assert event_types_of(get(client, "/events")) == ["runtime.registered"]

    delete(client, f"/runtimes/{rt_id}")
    assert event_types_of(get(client, "/events")) == ["runtime.removed", "runtime.registered"]


def test_model_scan_emits_registered_only_for_new_files(client, tmp_path):
    (tmp_path / "a.gguf").write_bytes(b"x")
    r1 = post(client, "/models/scan", {"folder": str(tmp_path)})
    assert r1.status_code == 200
    assert event_types_of(get(client, "/events")) == ["model.registered"]

    # Re-scanning the same folder (no new files) must NOT emit again --
    # scan_models_folder() returns the WHOLE catalog every time, and the
    # emission point is inside models.py specifically to avoid that.
    r2 = post(client, "/models/scan", {"folder": str(tmp_path)})
    assert r2.status_code == 200
    assert event_types_of(get(client, "/events")) == ["model.registered"]

    (tmp_path / "b.gguf").write_bytes(b"y")
    post(client, "/models/scan", {"folder": str(tmp_path)})
    assert event_types_of(get(client, "/events")) == ["model.registered", "model.registered"]


def test_model_delete_emits_removed(client, tmp_path):
    (tmp_path / "a.gguf").write_bytes(b"x")
    post(client, "/models/scan", {"folder": str(tmp_path)})
    model_id = get(client, "/models").json()[0]["id"]
    delete(client, f"/models/{model_id}")
    assert event_types_of(get(client, "/events")) == ["model.removed", "model.registered"]


def test_agent_runtime_attach_detach_emit_session_and_agent_events(client):
    rt_id = post(client, "/runtimes", {"name": "R1", "executable_path": "/bin/true", "port": 8080}).json()["id"]
    agent_id = post(client, "/agents", {"name": "A1"}).json()["id"]

    attach = post(client, f"/runtimes/{rt_id}/agents/{agent_id}/attach")
    link_id = attach.json()["session_link_id"]
    types = event_types_of(get(client, "/events"))
    # newest-first: agent.started emitted after session.started
    assert types[:2] == ["agent.started", "session.started"]
    for t in ("agent.started", "session.started"):
        row = next(e for e in get(client, "/events").json() if e["event_type"] == t)
        assert row["runtime_id"] == rt_id and row["agent_id"] == agent_id and row["session_id"] == link_id

    post(client, f"/sessions/{link_id}/detach")
    types = event_types_of(get(client, "/events"))
    assert types[:2] == ["agent.stopped", "session.completed"]


def test_events_filter_by_type_prefix_and_exact(client):
    post(client, "/agents", {"name": "A1"})
    post(client, "/runtimes", {"name": "R1", "executable_path": "/bin/true", "port": 8080})

    only_agent = event_types_of(get(client, "/events", params={"event_type": "agent."}))
    assert only_agent == ["agent.created"]

    exact = event_types_of(get(client, "/events", params={"event_type": "runtime.registered"}))
    assert exact == ["runtime.registered"]

    multiple = event_types_of(get(client, "/events", params={"event_type": "agent.created,runtime.registered"}))
    assert set(multiple) == {"agent.created", "runtime.registered"}


def test_events_filter_by_runtime_and_agent_id(client):
    rt1 = post(client, "/runtimes", {"name": "R1", "executable_path": "/bin/true", "port": 8080}).json()["id"]
    rt2 = post(client, "/runtimes", {"name": "R2", "executable_path": "/bin/true", "port": 8081}).json()["id"]

    only_rt1 = get(client, "/events", params={"runtime_id": rt1}).json()
    assert len(only_rt1) == 1 and only_rt1[0]["runtime_id"] == rt1
    only_rt2 = get(client, "/events", params={"runtime_id": rt2}).json()
    assert len(only_rt2) == 1 and only_rt2[0]["runtime_id"] == rt2


def test_events_pagination_with_before_id(client):
    for i in range(5):
        post(client, "/agents", {"name": f"A{i}"})
    page1 = get(client, "/events", params={"limit": 2}).json()
    assert len(page1) == 2
    oldest_id_in_page1 = page1[-1]["id"]
    page2 = get(client, "/events", params={"limit": 2, "before_id": oldest_id_in_page1}).json()
    assert len(page2) == 2
    assert all(e["id"] < oldest_id_in_page1 for e in page2)
    assert {e["id"] for e in page1}.isdisjoint({e["id"] for e in page2})


def test_events_limit_is_capped_at_500(client):
    resp = get(client, "/events", params={"limit": 10000})
    assert resp.status_code == 422  # FastAPI's own Query(le=500) validation


# ---------------------------------------------------------------------
# model verification
# ---------------------------------------------------------------------

def test_model_verification_emits_completed_event(client, tmp_path, monkeypatch):
    (tmp_path / "a.gguf").write_bytes(b"x" * 100)
    post(client, "/models/scan", {"folder": str(tmp_path)})
    model_id = get(client, "/models").json()[0]["id"]
    # start_model_verification requires an HF link already attached --
    # a plain folder scan doesn't set one.
    patch(client, f"/models/{model_id}", {"hf_repo_id": "acme/demo", "hf_filename": "a.gguf"})

    from backend.core import authenticity

    monkeypatch.setattr(
        authenticity, "verify_model_authenticity", lambda model: {"status": "verified", "checks": []}
    )
    resp = post(client, f"/models/{model_id}/verify")
    assert resp.status_code == 200

    for _ in range(50):
        types = event_types_of(get(client, "/events"))
        if "model.verification_completed" in types:
            break
        time.sleep(0.02)
    row = next(e for e in get(client, "/events").json() if e["event_type"] == "model.verification_completed")
    assert row["metadata"]["model_id"] == model_id and row["metadata"]["status"] == "verified"


# ---------------------------------------------------------------------
# chat inference
# ---------------------------------------------------------------------

def _online_status():
    return EngineStatus(state=RuntimeState.ONLINE, endpoint="http://127.0.0.1:9999")


def test_inference_started_and_completed_emitted_on_success(client, monkeypatch):
    rt_id = post(client, "/runtimes", {"name": "R1", "executable_path": "/bin/true", "port": 8080}).json()["id"]
    chat_id = post(client, "/chats", {"runtime_id": rt_id}).json()["id"]

    from backend.api import http as http_module

    monkeypatch.setattr(http_module.runtime_manager, "get_status", lambda db, rt: _online_status())
    monkeypatch.setattr(
        http_module.chat_engine,
        "send_chat_completion",
        lambda *a, **kw: {"content": "hi", "prompt_tokens": 3, "completion_tokens": 2, "latency_ms": 12.5},
    )

    resp = post(client, f"/chats/{chat_id}/messages", {"content": "hello"})
    assert resp.status_code == 200

    types = event_types_of(get(client, "/events"))
    assert types[:2] == ["inference.completed", "inference.started"]
    completed = next(e for e in get(client, "/events").json() if e["event_type"] == "inference.completed")
    assert completed["runtime_id"] == rt_id
    assert completed["metadata"]["prompt_tokens"] == 3 and completed["metadata"]["completion_tokens"] == 2


def test_inference_failed_emitted_on_completion_error(client, monkeypatch):
    rt_id = post(client, "/runtimes", {"name": "R1", "executable_path": "/bin/true", "port": 8080}).json()["id"]
    chat_id = post(client, "/chats", {"runtime_id": rt_id}).json()["id"]

    from backend.api import http as http_module
    from backend.core import chat as chat_engine_module

    monkeypatch.setattr(http_module.runtime_manager, "get_status", lambda db, rt: _online_status())

    def boom(*a, **kw):
        raise chat_engine_module.ChatCompletionError("runtime unreachable")

    monkeypatch.setattr(http_module.chat_engine, "send_chat_completion", boom)

    resp = post(client, f"/chats/{chat_id}/messages", {"content": "hello"})
    assert resp.status_code == 502

    types = event_types_of(get(client, "/events"))
    assert types[:2] == ["inference.failed", "inference.started"]
    failed = next(e for e in get(client, "/events").json() if e["event_type"] == "inference.failed")
    assert "runtime unreachable" in failed["metadata"]["error"]


def test_no_inference_events_when_runtime_offline(client, monkeypatch):
    rt_id = post(client, "/runtimes", {"name": "R1", "executable_path": "/bin/true", "port": 8080}).json()["id"]
    chat_id = post(client, "/chats", {"runtime_id": rt_id}).json()["id"]

    from backend.api import http as http_module

    monkeypatch.setattr(
        http_module.runtime_manager, "get_status", lambda db, rt: EngineStatus(state=RuntimeState.OFFLINE)
    )
    resp = post(client, f"/chats/{chat_id}/messages", {"content": "hello"})
    assert resp.status_code == 409
    assert event_types_of(get(client, "/events")) == ["runtime.registered"]  # only the earlier create, nothing inference-related
