"""
tests/backend/test_permissions_api.py

api/permissions.py end to end through FastAPI's TestClient. `POST
/check` runs in a real threadpool worker (TestClient talks to the real
ASGI app, and FastAPI's `def` routes go through `run_in_threadpool`), so
these tests resolve a pending request from a second Python thread while
the main thread's HTTP call to /check is still blocked inside the
request -- the same concurrency the real frontend/backend pair will have.
"""

import threading
import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.api import http
from backend.api import permissions as permissions_api
from backend.core.permissions.engine import PermissionEngine
from backend.core.security import get_or_create_access_token
from backend.storage import db as storage_db
from backend.storage.db import Agent, LlmAgentSession, PermissionGrant, Runtime

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
def engine(monkeypatch):
    e = PermissionEngine(default_timeout=5)
    monkeypatch.setattr(permissions_api, "permission_engine", e)
    return e


@pytest.fixture
def client(engine):
    app = FastAPI()
    app.include_router(http.router, dependencies=[Depends(http.require_token)])
    app.include_router(permissions_api.router)
    return TestClient(app)


def get(c, path, **kw):
    return c.get(f"/api/v1{path}", headers=AUTH, **kw)


def post(c, path, body=None, **kw):
    return c.post(f"/api/v1{path}", headers=AUTH, json=body, **kw)


def delete(c, path, **kw):
    return c.delete(f"/api/v1{path}", headers=AUTH, **kw)


def make_session():
    with storage_db.SessionLocal() as s:
        agent = Agent(name="a", agent_type="generic")
        runtime = Runtime(name="r", executable_path="/bin/true", host="127.0.0.1", port=1)
        s.add_all([agent, runtime])
        s.commit()
        sess = LlmAgentSession(runtime_id=runtime.id, agent_id=agent.id)
        s.add(sess)
        s.commit()
        return sess.id, agent.id


def resolve_when_pending(client, decision, timeout=5):
    """Waits (via GET /pending) for exactly one pending request, then
    resolves it. Runs in a background thread so the caller can issue the
    blocking POST /check on the main thread, same as the real client/
    server pair."""
    result = {}

    def go():
        end = time.time() + timeout
        while time.time() < end:
            pending = get(client, "/permissions/pending").json()
            if pending:
                result["id"] = pending[0]["id"]
                result["resp"] = post(client, f"/permissions/requests/{pending[0]['id']}/resolve", {"decision": decision})
                return
            time.sleep(0.02)

    t = threading.Thread(target=go)
    t.start()
    return t, result


# ---------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/permissions/check"), ("get", "/permissions/pending"),
        ("post", "/permissions/requests/x/resolve"), ("get", "/permissions/grants"), ("delete", "/permissions/grants/1"),
    ],
)
def test_every_route_requires_the_token(client, method, path):
    r = getattr(client, method)(f"/api/v1{path}")
    assert r.status_code == 401


# ---------------------------------------------------------------------
# check: live round trip
# ---------------------------------------------------------------------

def test_check_blocks_then_returns_the_live_decision(client):
    t, result = resolve_when_pending(client, "allow_once")
    r = post(client, "/permissions/check", {"scope_type": "mcp_server", "scope_key": "1", "risk_level": "LOW", "timeout_seconds": 5})
    t.join(5)
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "allow" and body["decision_kind"] == "allow_once" and body["source"] == "live_decision"
    assert result["resp"].status_code == 200 and result["resp"].json() == {"resolved": True}


def test_check_with_no_answer_times_out_as_deny(client):
    t0 = time.time()
    r = post(client, "/permissions/check", {"scope_type": "mcp_server", "scope_key": "2", "risk_level": "LOW", "timeout_seconds": 0.5})
    assert time.time() - t0 < 3
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "deny" and body["source"] == "timeout"


def test_pending_shows_the_full_request_while_waiting(client):
    # No concurrent resolver here on purpose: inspecting /pending races
    # against anything that might resolve the request, so this test
    # resolves it itself only AFTER it has observed the fields it came
    # to check.
    def check():
        post(client, "/permissions/check", {
            "scope_type": "mcp_tool", "scope_key": "3:delete_all", "risk_level": "CRITICAL",
            "description": "delete every file in the workspace", "timeout_seconds": 10,
        })

    ct = threading.Thread(target=check)
    ct.start()
    end = time.time() + 10
    pending = []
    while not pending and time.time() < end:
        pending = get(client, "/permissions/pending").json()
        time.sleep(0.02)
    assert len(pending) == 1
    assert pending[0]["scope_key"] == "3:delete_all" and pending[0]["risk_level"] == "CRITICAL"
    assert pending[0]["description"] == "delete every file in the workspace"
    post(client, f"/permissions/requests/{pending[0]['id']}/resolve", {"decision": "deny"})
    ct.join(10)


def test_resolving_an_unknown_request_is_404(client):
    r = post(client, "/permissions/requests/does-not-exist/resolve", {"decision": "allow_once"})
    assert r.status_code == 404


def test_resolving_twice_the_second_is_404(client):
    t, result = resolve_when_pending(client, "allow_once")
    post(client, "/permissions/check", {"scope_type": "mcp_server", "scope_key": "5", "risk_level": "LOW", "timeout_seconds": 5})
    t.join(5)
    rid = result["id"]
    r2 = post(client, f"/permissions/requests/{rid}/resolve", {"decision": "deny"})
    assert r2.status_code == 404


def test_invalid_decision_is_422(client):
    r = post(client, "/permissions/requests/whatever/resolve", {"decision": "sure_why_not"})
    assert r.status_code == 422


def test_check_validates_scope_type_and_risk_level(client):
    assert post(client, "/permissions/check", {"scope_type": "shell", "scope_key": "x", "risk_level": "LOW", "timeout_seconds": 1}).status_code == 422
    assert post(client, "/permissions/check", {"scope_type": "mcp_server", "scope_key": "x", "risk_level": "SUPER", "timeout_seconds": 1}).status_code == 422


def test_check_timeout_seconds_is_bounded(client):
    assert post(client, "/permissions/check", {"scope_type": "mcp_server", "scope_key": "x", "risk_level": "LOW", "timeout_seconds": 0}).status_code == 422
    assert post(client, "/permissions/check", {"scope_type": "mcp_server", "scope_key": "x", "risk_level": "LOW", "timeout_seconds": 99999}).status_code == 422


def test_check_with_invalid_session_id_is_400_not_500(client):
    t, _ = resolve_when_pending(client, "allow_once")
    r = post(client, "/permissions/check", {"scope_type": "mcp_server", "scope_key": "x", "risk_level": "LOW", "session_id": 999999, "timeout_seconds": 5})
    t.join(5)
    assert r.status_code == 400
    assert get(client, "/permissions/grants").json() == []


# ---------------------------------------------------------------------
# standing grants via the API
# ---------------------------------------------------------------------

def test_allow_always_then_second_check_returns_instantly_from_the_grant(client):
    t, _ = resolve_when_pending(client, "allow_always")
    r1 = post(client, "/permissions/check", {"scope_type": "mcp_server", "scope_key": "7", "risk_level": "HIGH", "timeout_seconds": 5})
    t.join(5)
    assert r1.json()["decision"] == "allow"

    t0 = time.time()
    r2 = post(client, "/permissions/check", {"scope_type": "mcp_server", "scope_key": "7", "risk_level": "HIGH", "timeout_seconds": 5})
    assert time.time() - t0 < 1  # answered from the grant, no waiting
    assert r2.json() == {"decision": "allow", "source": "existing_grant", "grant_id": r1.json()["grant_id"], "request_id": None}
    assert get(client, "/permissions/pending").json() == []


def test_allow_session_scoped_to_the_given_session(client):
    sid, aid = make_session()
    t, _ = resolve_when_pending(client, "allow_session")
    r1 = post(client, "/permissions/check", {"scope_type": "mcp_tool", "scope_key": "9:write", "risk_level": "HIGH", "session_id": sid, "agent_id": aid, "timeout_seconds": 5})
    t.join(5)
    assert r1.json()["decision"] == "allow"

    r2 = post(client, "/permissions/check", {"scope_type": "mcp_tool", "scope_key": "9:write", "risk_level": "HIGH", "session_id": sid, "timeout_seconds": 1})
    assert r2.json()["source"] == "existing_grant"


# ---------------------------------------------------------------------
# grants list + revoke
# ---------------------------------------------------------------------

def test_grants_list_and_filters(client):
    t1, _ = resolve_when_pending(client, "allow_always")
    post(client, "/permissions/check", {"scope_type": "mcp_server", "scope_key": "20", "risk_level": "LOW", "timeout_seconds": 5})
    t1.join(5)
    t2, _ = resolve_when_pending(client, "deny")
    post(client, "/permissions/check", {"scope_type": "mcp_tool", "scope_key": "20:x", "risk_level": "HIGH", "timeout_seconds": 5})
    t2.join(5)

    all_grants = get(client, "/permissions/grants").json()
    assert len(all_grants) == 2
    assert all_grants[0]["scope_key"] == "20:x"  # newest first

    servers_only = get(client, "/permissions/grants?scope_type=mcp_server").json()
    assert [g["scope_key"] for g in servers_only] == ["20"]

    active_only = get(client, "/permissions/grants?active_only=true").json()
    assert [g["scope_key"] for g in active_only] == ["20"]  # allow_always is active; deny is not
    assert active_only[0]["active"] is True


def test_grants_scope_key_filter_and_bad_scope_type(client):
    t, _ = resolve_when_pending(client, "allow_always")
    post(client, "/permissions/check", {"scope_type": "mcp_server", "scope_key": "33", "risk_level": "LOW", "timeout_seconds": 5})
    t.join(5)
    assert [g["scope_key"] for g in get(client, "/permissions/grants?scope_key=33").json()] == ["33"]
    assert get(client, "/permissions/grants?scope_type=nonsense").status_code == 400


def test_revoke_grant_via_api(client):
    t, _ = resolve_when_pending(client, "allow_always")
    r1 = post(client, "/permissions/check", {"scope_type": "mcp_server", "scope_key": "40", "risk_level": "LOW", "timeout_seconds": 5})
    t.join(5)
    grant_id = r1.json()["grant_id"]

    r = delete(client, f"/permissions/grants/{grant_id}")
    assert r.status_code == 200 and r.json()["active"] is False

    # must ask again now
    t2, result = resolve_when_pending(client, "deny")
    r2 = post(client, "/permissions/check", {"scope_type": "mcp_server", "scope_key": "40", "risk_level": "LOW", "timeout_seconds": 5})
    t2.join(5)
    assert r2.json()["decision"] == "deny"


def test_revoke_unknown_grant_is_404(client):
    assert delete(client, "/permissions/grants/999999").status_code == 404


# ---------------------------------------------------------------------
# events + the real app
# ---------------------------------------------------------------------

def test_check_and_resolve_are_recorded_as_events(client):
    t, _ = resolve_when_pending(client, "allow_session")
    post(client, "/permissions/check", {"scope_type": "mcp_server", "scope_key": "50", "risk_level": "MEDIUM", "timeout_seconds": 5})
    t.join(5)
    types = [e["event_type"] for e in reversed(get(client, "/events?event_type=permission.&limit=20").json())]
    assert types == ["permission.requested", "permission.approved"]


def test_main_app_mounts_the_permissions_router():
    from backend.main import app

    with TestClient(app) as c:
        token = c.get("/api/v1/auth/bootstrap-token").json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert c.get("/api/v1/permissions/pending").status_code == 401
        assert c.get("/api/v1/permissions/pending", headers=auth).status_code == 200
        assert c.get("/api/v1/permissions/grants", headers=auth).status_code == 200
        r = c.post("/api/v1/permissions/check", headers=auth, json={"scope_type": "mcp_server", "scope_key": "1", "risk_level": "LOW", "timeout_seconds": 0.3})
        assert r.status_code == 200 and r.json()["decision"] == "deny"
