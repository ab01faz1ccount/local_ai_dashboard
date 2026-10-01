"""
tests/backend/test_mcp_api.py

api/mcp.py end to end through FastAPI's TestClient, driving a real
McpManager against the real stdio fixture server: CRUD, validation
errors, secret write-only-ness, lifecycle routes, and the events each
action leaves in the log.
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.api import http
from backend.api import mcp as mcp_api
from backend.core.mcp.manager import McpManager
from backend.core.security import get_or_create_access_token
from backend.storage import db as storage_db
from backend.storage.db import McpServer

AUTH = {"Authorization": f"Bearer {get_or_create_access_token()}"}
FIXTURE = str(Path(__file__).with_name("mcp_fixture_server.py"))


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
def manager(monkeypatch):
    m = McpManager(connect_timeout=20, ping_interval=0.4, ping_timeout=3)
    monkeypatch.setattr(mcp_api, "mcp_manager", m)
    yield m
    m.shutdown()


@pytest.fixture
def client(manager):
    app = FastAPI()
    app.include_router(http.router, dependencies=[Depends(http.require_token)])
    app.include_router(mcp_api.router)
    return TestClient(app)


def get(c, path, **kw):
    return c.get(f"/api/v1{path}", headers=AUTH, **kw)


def post(c, path, body=None, **kw):
    return c.post(f"/api/v1{path}", headers=AUTH, json=body, **kw)


def patch(c, path, body=None, **kw):
    return c.patch(f"/api/v1{path}", headers=AUTH, json=body, **kw)


def delete(c, path, **kw):
    return c.delete(f"/api/v1{path}", headers=AUTH, **kw)


def fixture_body(name="fx", **over):
    return {"name": name, "transport": "stdio", "command": sys.executable, "args": [FIXTURE], **over}


def mcp_events(c):
    return [e["event_type"] for e in reversed(get(c, "/events?event_type=mcp.&limit=100").json())]


# ---------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/mcp/servers"), ("post", "/mcp/servers"), ("get", "/mcp/servers/1"), ("patch", "/mcp/servers/1"),
        ("delete", "/mcp/servers/1"), ("post", "/mcp/servers/1/connect"), ("post", "/mcp/servers/1/disconnect"),
        ("get", "/mcp/servers/1/tools"),
    ],
)
def test_every_route_requires_the_token(client, method, path):
    r = getattr(client, method)(f"/api/v1{path}")
    assert r.status_code == 401
    r = getattr(client, method)(f"/api/v1{path}", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


# ---------------------------------------------------------------------
# create / read
# ---------------------------------------------------------------------

def test_create_then_list_and_get(client):
    r = post(client, "/mcp/servers", fixture_body(description="test", env={"A_KEY": "v1"}))
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "fx" and body["status"] == "DISCONNECTED" and body["enabled"] is True
    assert body["args"] == [FIXTURE] and body["env_keys"] == ["A_KEY"] and body["tools_count"] == 0
    assert body["source_system"] == "local"

    assert [s["id"] for s in get(client, "/mcp/servers").json()] == [body["id"]]
    assert get(client, f"/mcp/servers/{body['id']}").json()["name"] == "fx"


def test_secret_values_are_never_returned(client):
    secret_env, secret_hdr = "sk-env-secret-777", "Bearer hdr-secret-888"
    a = post(client, "/mcp/servers", fixture_body("a", env={"API_KEY": secret_env})).json()
    b = post(client, "/mcp/servers", {"name": "b", "transport": "http", "url": "http://127.0.0.1:1/mcp", "headers": {"Authorization": secret_hdr}}).json()
    assert b["header_keys"] == ["Authorization"]
    for path in ("/mcp/servers", f"/mcp/servers/{a['id']}", f"/mcp/servers/{b['id']}"):
        text = json.dumps(get(client, path).json())
        assert secret_env not in text and secret_hdr not in text
    # ...nor in the events log
    assert secret_env not in json.dumps(get(client, "/events").json())


def test_create_is_recorded_as_an_event(client):
    post(client, "/mcp/servers", fixture_body())
    assert mcp_events(client) == ["mcp.server_registered"]


@pytest.mark.parametrize(
    "over",
    [
        {"command": "bash", "args": ["-c", "id"]},
        {"command": "/bin/sh"},
        {"command": "npx; id"},
        {"command": "python", "args": ["-c", "print(1)"]},
        {"command": "not-a-launcher"},
        {"env": {"LD_PRELOAD": "/tmp/x.so"}},
        {"env": {"PATH": "/tmp"}},
        {"env": {"bad key": "x"}},
        {"transport": "carrier-pigeon"},
        {"name": "   "},
        {"args": ["ok", "x\x00y"]},
    ],
)
def test_dangerous_or_malformed_stdio_configs_are_400(client, over):
    r = post(client, "/mcp/servers", fixture_body(**over))
    assert r.status_code == 400, r.text
    assert get(client, "/mcp/servers").json() == []  # nothing was stored
    assert mcp_events(client) == []


@pytest.mark.parametrize(
    "over",
    [
        {"url": "ftp://x.com/a"},
        {"url": "https://u:p@example.com/mcp"},
        {"url": None},
        {"url": "http://x.com/mcp", "headers": {"X": "a\r\nInjected: 1"}},
    ],
)
def test_bad_remote_configs_are_400(client, over):
    body = {"name": "r", "transport": "http", **over}
    assert post(client, "/mcp/servers", body).status_code == 400


def test_duplicate_name_is_409(client):
    assert post(client, "/mcp/servers", fixture_body("same")).status_code == 201
    r = post(client, "/mcp/servers", fixture_body("same"))
    assert r.status_code == 409
    assert len(get(client, "/mcp/servers").json()) == 1


def test_remote_flag(client):
    local = post(client, "/mcp/servers", {"name": "l", "transport": "http", "url": "http://localhost:9/mcp"}).json()
    remote = post(client, "/mcp/servers", {"name": "r", "transport": "sse", "url": "https://mcp.example.com/sse"}).json()
    stdio = post(client, "/mcp/servers", fixture_body()).json()
    assert (local["is_remote"], remote["is_remote"], stdio["is_remote"]) == (False, True, False)


def test_unknown_ids_are_404(client):
    for r in (
        get(client, "/mcp/servers/999"), patch(client, "/mcp/servers/999", {"name": "x"}), delete(client, "/mcp/servers/999"),
        post(client, "/mcp/servers/999/connect"), post(client, "/mcp/servers/999/disconnect"), get(client, "/mcp/servers/999/tools"),
    ):
        assert r.status_code == 404


# ---------------------------------------------------------------------
# update
# ---------------------------------------------------------------------

def _stored(server_id):
    with storage_db.SessionLocal() as s:
        row = s.get(McpServer, server_id)
        return {"env": dict(row.env_json), "headers": dict(row.headers_json), "args": row.args_json["args"], "command": row.command,
                "transport": row.transport, "url": row.url, "enabled": row.enabled}


def test_patch_env_is_a_partial_merge_with_null_deleting(client):
    sid = post(client, "/mcp/servers", fixture_body(env={"KEEP": "k", "CHANGE": "old", "DROP": "d"})).json()["id"]
    r = patch(client, f"/mcp/servers/{sid}", {"env": {"CHANGE": "new", "DROP": None, "ADDED": "a"}})
    assert r.status_code == 200 and r.json()["env_keys"] == ["ADDED", "CHANGE", "KEEP"]
    assert _stored(sid)["env"] == {"KEEP": "k", "CHANGE": "new", "ADDED": "a"}


def test_patch_headers_merge_the_same_way(client):
    sid = post(client, "/mcp/servers", {"name": "r", "transport": "http", "url": "http://127.0.0.1:1/m", "headers": {"A": "1", "B": "2"}}).json()["id"]
    patch(client, f"/mcp/servers/{sid}", {"headers": {"B": None, "C": "3"}})
    assert _stored(sid)["headers"] == {"A": "1", "C": "3"}


def test_patch_revalidates_the_merged_result(client):
    sid = post(client, "/mcp/servers", fixture_body()).json()["id"]
    for bad in ({"command": "bash"}, {"args": ["-c", "x"], "command": "python"}, {"env": {"PATH": "/x"}}, {"url": "javascript:1", "transport": "http"}):
        assert patch(client, f"/mcp/servers/{sid}", bad).status_code == 400, bad
    assert _stored(sid)["command"] == sys.executable and _stored(sid)["env"] == {}  # rejected patch changed nothing


def test_patch_name_description_and_duplicate_name(client):
    a = post(client, "/mcp/servers", fixture_body("a")).json()["id"]
    post(client, "/mcp/servers", fixture_body("b"))
    r = patch(client, f"/mcp/servers/{a}", {"name": "renamed", "description": "d"})
    assert r.json()["name"] == "renamed" and r.json()["description"] == "d"
    assert patch(client, f"/mcp/servers/{a}", {"name": "b"}).status_code == 409
    assert get(client, f"/mcp/servers/{a}").json()["name"] == "renamed"


def test_patch_can_switch_transport_and_drops_stale_fields(client):
    sid = post(client, "/mcp/servers", fixture_body(env={"K": "v"})).json()["id"]
    r = patch(client, f"/mcp/servers/{sid}", {"transport": "http", "url": "http://127.0.0.1:1/mcp"})
    assert r.status_code == 200
    s = _stored(sid)
    assert s["transport"] == "http" and s["command"] is None and s["env"] == {} and s["url"] == "http://127.0.0.1:1/mcp"


def test_connection_fields_cannot_change_while_connected_but_metadata_can(client):
    sid = post(client, "/mcp/servers", fixture_body()).json()["id"]
    assert post(client, f"/mcp/servers/{sid}/connect").json()["state"] == "CONNECTED"
    for body in ({"command": "node"}, {"args": []}, {"env": {"A": "b"}}, {"url": "http://x.com"}, {"transport": "http"}):
        assert patch(client, f"/mcp/servers/{sid}", body).status_code == 400, body
    assert patch(client, f"/mcp/servers/{sid}", {"name": "still-ok"}).status_code == 200
    assert get(client, f"/mcp/servers/{sid}").json()["status"] == "CONNECTED"


def test_disabling_a_connected_server_disconnects_it(client, manager):
    sid = post(client, "/mcp/servers", fixture_body()).json()["id"]
    post(client, f"/mcp/servers/{sid}/connect")
    assert manager.is_connected(sid)
    r = patch(client, f"/mcp/servers/{sid}", {"enabled": False})
    assert r.json()["enabled"] is False and r.json()["status"] == "DISCONNECTED"
    assert not manager.is_connected(sid)
    assert mcp_events(client) == ["mcp.server_registered", "mcp.connected", "mcp.disconnected"]


# ---------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------

def test_connect_tools_disconnect_round_trip(client, manager):
    sid = post(client, "/mcp/servers", fixture_body()).json()["id"]

    r = post(client, f"/mcp/servers/{sid}/connect")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "CONNECTED" and body["tools_count"] == 6 and body["error"] is None
    assert body["server"]["status"] == "CONNECTED" and body["server"]["server_info"]["name"] == "fixture-server"

    tools = get(client, f"/mcp/servers/{sid}/tools").json()
    assert tools["server_id"] == sid
    assert sorted(t["name"] for t in tools["tools"]) == ["add", "delete_file", "echo", "env_value", "fail", "slow"]
    assert all({"name", "description", "input_schema"} <= t.keys() for t in tools["tools"])

    r = post(client, f"/mcp/servers/{sid}/disconnect")
    assert r.json()["state"] == "DISCONNECTED" and r.json()["server"]["status"] == "DISCONNECTED"
    assert not manager.is_connected(sid)
    assert get(client, f"/mcp/servers/{sid}/tools").status_code == 409


def test_tools_of_a_never_connected_server_is_409(client):
    sid = post(client, "/mcp/servers", fixture_body()).json()["id"]
    r = get(client, f"/mcp/servers/{sid}/tools")
    assert r.status_code == 409


def test_a_failed_connect_is_a_200_with_error_state_and_a_failure_event(client):
    sid = post(client, "/mcp/servers", fixture_body(args=[FIXTURE, "--exit-now"])).json()["id"]
    r = post(client, f"/mcp/servers/{sid}/connect")
    assert r.status_code == 200
    assert r.json()["state"] == "ERROR" and r.json()["error"]
    assert get(client, f"/mcp/servers/{sid}").json()["status"] == "ERROR"
    assert get(client, f"/mcp/servers/{sid}").json()["last_error"] == r.json()["error"]
    assert mcp_events(client) == ["mcp.server_registered", "mcp.connect_failed"]


def test_connecting_a_disabled_server_is_refused(client, manager):
    sid = post(client, "/mcp/servers", fixture_body(enabled=False)).json()["id"]
    r = post(client, f"/mcp/servers/{sid}/connect")
    assert r.status_code == 400
    assert not manager.is_connected(sid) and mcp_events(client) == ["mcp.server_registered"]


def test_connect_twice_is_harmless(client):
    sid = post(client, "/mcp/servers", fixture_body()).json()["id"]
    post(client, f"/mcp/servers/{sid}/connect")
    r = post(client, f"/mcp/servers/{sid}/connect")
    assert r.json()["state"] == "CONNECTED"
    assert mcp_events(client).count("mcp.connected") == 1


def test_error_messages_do_not_leak_secrets_through_the_api(client, manager, monkeypatch):
    secret = "sk-api-secret-424242"
    monkeypatch.setattr(manager, "_open_transport", lambda cfg: (_ for _ in ()).throw(RuntimeError(f"denied {secret}")))
    sid = post(client, "/mcp/servers", fixture_body(env={"API_KEY": secret})).json()["id"]
    r = post(client, f"/mcp/servers/{sid}/connect")
    assert secret not in json.dumps(r.json())
    assert secret not in json.dumps(get(client, "/events").json())
    assert secret not in json.dumps(get(client, f"/mcp/servers/{sid}").json())


# ---------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------

def test_delete_disconnected_server(client):
    sid = post(client, "/mcp/servers", fixture_body()).json()["id"]
    assert delete(client, f"/mcp/servers/{sid}").json() == {"deleted": True}
    assert get(client, "/mcp/servers").json() == []
    assert mcp_events(client) == ["mcp.server_registered", "mcp.server_removed"]


def test_delete_a_connected_server_stops_it_first(client, manager):
    sid = post(client, "/mcp/servers", fixture_body()).json()["id"]
    post(client, f"/mcp/servers/{sid}/connect")
    assert delete(client, f"/mcp/servers/{sid}").status_code == 200
    assert not manager.is_connected(sid)
    assert get(client, f"/mcp/servers/{sid}").status_code == 404
    assert mcp_events(client) == ["mcp.server_registered", "mcp.connected", "mcp.disconnected", "mcp.server_removed"]


def test_removed_event_survives_the_row_it_describes(client):
    sid = post(client, "/mcp/servers", fixture_body("gone")).json()["id"]
    delete(client, f"/mcp/servers/{sid}")
    ev = [e for e in get(client, "/events?event_type=mcp.server_removed").json()][0]
    assert ev["metadata"]["name"] == "gone" and ev["metadata"]["server_id"] == sid


# ---------------------------------------------------------------------
# the real app (main.py wiring)
# ---------------------------------------------------------------------

def test_main_app_mounts_the_router_and_resets_stale_rows_on_startup():
    from backend.main import app

    with storage_db.SessionLocal() as s:
        s.add(McpServer(name="stale", transport="stdio", command=sys.executable, status="CONNECTED", tools_count=9))
        s.commit()

    with TestClient(app) as c:
        token = c.get("/api/v1/auth/bootstrap-token").json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert c.get("/api/v1/mcp/servers").status_code == 401
        rows = c.get("/api/v1/mcp/servers", headers=auth).json()
        stale = [r for r in rows if r["name"] == "stale"][0]
        assert stale["status"] == "DISCONNECTED" and stale["tools_count"] == 0  # startup hook ran
        assert "mcp.connected" in [e["event_type"] for e in c.get("/api/v1/events/types", headers=auth).json()]
