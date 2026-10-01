"""
tests/backend/test_tools_api.py

api/tools.py end to end through FastAPI's TestClient: listing, filters,
the enabled toggle (the only thing this router lets a user change), and
that connection status is reported live from the real McpManager rather
than a stale DB flag.
"""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.api import http
from backend.api import tools as tools_api
from backend.core.mcp.manager import McpManager
from backend.core.security import get_or_create_access_token
from backend.core.tools import sync_server_tools
from backend.storage import db as storage_db
from backend.storage.db import McpServer

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
def manager(monkeypatch):
    m = McpManager()
    monkeypatch.setattr(tools_api, "mcp_manager", m)
    return m


@pytest.fixture
def client(manager):
    app = FastAPI()
    app.include_router(http.router, dependencies=[Depends(http.require_token)])
    app.include_router(tools_api.router)
    return TestClient(app)


def get(c, path, **kw):
    return c.get(f"/api/v1{path}", headers=AUTH, **kw)


def patch(c, path, body=None, **kw):
    return c.patch(f"/api/v1{path}", headers=AUTH, json=body, **kw)


def make_server_with_tools(names_and_risks, name="fx"):
    with storage_db.SessionLocal() as s:
        row = McpServer(name=name, transport="stdio", command="npx", status="CONNECTED")
        s.add(row)
        s.commit()
        sync_server_tools(
            s, row.id,
            [{"name": n, "description": f"{n} tool", "annotations": ann} for n, ann in names_and_risks],
        )
        return row.id


@pytest.mark.parametrize("method,path", [("get", "/tools"), ("get", "/tools/1"), ("patch", "/tools/1")])
def test_every_route_requires_the_token(client, method, path):
    assert getattr(client, method)(f"/api/v1{path}").status_code == 401


def test_list_tools_includes_server_name_and_status(client):
    sid = make_server_with_tools([("read", {"readOnlyHint": True}), ("write", {"destructiveHint": True})])
    rows = get(client, "/tools").json()
    assert len(rows) == 2
    names = {r["name"] for r in rows}
    assert names == {"read", "write"}
    read = next(r for r in rows if r["name"] == "read")
    assert read["server_name"] == "fx" and read["server_status"] == "CONNECTED" and read["mcp_server_id"] == sid
    assert read["risk_level"] == "LOW"
    write = next(r for r in rows if r["name"] == "write")
    assert write["risk_level"] == "CRITICAL"


def test_connected_reflects_the_live_manager_not_the_db_status(client, manager):
    make_server_with_tools([("a", {})])  # DB row says status=CONNECTED, but nothing is actually live
    row = get(client, "/tools").json()[0]
    assert row["connected"] is False  # manager has no live session for this server id


def test_filter_by_mcp_server_id(client):
    sid1 = make_server_with_tools([("a", {})], name="s1")
    sid2 = make_server_with_tools([("b", {})], name="s2")
    assert [r["name"] for r in get(client, f"/tools?mcp_server_id={sid1}").json()] == ["a"]
    assert [r["name"] for r in get(client, f"/tools?mcp_server_id={sid2}").json()] == ["b"]


def test_filter_enabled_only(client):
    make_server_with_tools([("a", {}), ("b", {})])
    tool_id = next(r["id"] for r in get(client, "/tools").json() if r["name"] == "b")
    patch(client, f"/tools/{tool_id}", {"enabled": False})
    assert [r["name"] for r in get(client, "/tools?enabled_only=true").json()] == ["a"]
    assert len(get(client, "/tools").json()) == 2


def test_get_single_tool(client):
    make_server_with_tools([("only", {})])
    tool_id = get(client, "/tools").json()[0]["id"]
    row = get(client, f"/tools/{tool_id}").json()
    assert row["name"] == "only"


def test_get_unknown_tool_is_404(client):
    assert get(client, "/tools/999999").status_code == 404


def test_patch_toggles_enabled(client):
    make_server_with_tools([("a", {})])
    tool_id = get(client, "/tools").json()[0]["id"]
    r = patch(client, f"/tools/{tool_id}", {"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert get(client, f"/tools/{tool_id}").json()["enabled"] is False
    r2 = patch(client, f"/tools/{tool_id}", {"enabled": True})
    assert r2.json()["enabled"] is True


def test_patch_unknown_tool_is_404(client):
    assert patch(client, "/tools/999999", {"enabled": False}).status_code == 404


def test_patch_requires_enabled_field(client):
    make_server_with_tools([("a", {})])
    tool_id = get(client, "/tools").json()[0]["id"]
    assert patch(client, f"/tools/{tool_id}", {}).status_code == 422


def test_main_app_mounts_the_tools_router():
    from backend.main import app

    with TestClient(app) as c:
        token = c.get("/api/v1/auth/bootstrap-token").json()["access_token"]
        assert c.get("/api/v1/tools", headers={"Authorization": f"Bearer {token}"}).status_code == 200
