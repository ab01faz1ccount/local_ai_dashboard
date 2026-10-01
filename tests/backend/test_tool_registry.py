"""
tests/backend/test_tool_registry.py

core/tools/registry.py: upserting an MCP server's tool list into the
`tools` table, against real tool dicts (the exact shape
core.mcp.manager._tool_to_dict produces).
"""

import pytest
from sqlalchemy.orm import sessionmaker

from backend.core.tools import sync_server_tools
from backend.storage import db as storage_db
from backend.storage.db import McpServer, Tool


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


@pytest.fixture
def server(db) -> int:
    row = McpServer(name="fx", transport="stdio", command="npx")
    db.add(row)
    db.commit()
    return row.id


def tool(name, **over):
    return {"name": name, "description": f"{name} tool", "input_schema": {"type": "object"}, **over}


def test_sync_creates_rows_with_derived_risk(db, server):
    rows = sync_server_tools(db, server, [
        tool("read", annotations={"readOnlyHint": True}),
        tool("write", annotations={"destructiveHint": True}),
        tool("noop"),
    ])
    by_name = {r.name: r for r in rows}
    assert by_name["read"].risk_level == "LOW"
    assert by_name["write"].risk_level == "CRITICAL"
    assert by_name["noop"].risk_level == "HIGH"
    assert all(r.mcp_server_id == server and r.enabled for r in rows)


def test_sync_is_idempotent(db, server):
    sync_server_tools(db, server, [tool("read")])
    sync_server_tools(db, server, [tool("read")])
    assert db.query(Tool).filter(Tool.mcp_server_id == server).count() == 1


def test_resync_updates_description_and_schema_and_risk(db, server):
    sync_server_tools(db, server, [tool("thing", description="v1", annotations={"readOnlyHint": True})])
    rows = sync_server_tools(db, server, [
        tool("thing", description="v2", input_schema={"type": "object", "properties": {"x": {}}}, annotations={"destructiveHint": True})
    ])
    row = rows[0]
    assert row.description == "v2" and row.risk_level == "CRITICAL"
    assert row.input_schema_json == {"type": "object", "properties": {"x": {}}}


def test_resync_removes_tools_no_longer_listed(db, server):
    sync_server_tools(db, server, [tool("a"), tool("b")])
    rows = sync_server_tools(db, server, [tool("a")])
    assert [r.name for r in rows] == ["a"]
    assert db.query(Tool).filter(Tool.mcp_server_id == server).count() == 1


def test_resync_never_touches_enabled(db, server):
    sync_server_tools(db, server, [tool("a")])
    row = db.query(Tool).filter(Tool.name == "a").one()
    row.enabled = False
    db.commit()
    rows = sync_server_tools(db, server, [tool("a", description="updated")])
    assert rows[0].enabled is False and rows[0].description == "updated"


def test_two_servers_do_not_collide_on_the_same_tool_name(db):
    s1 = McpServer(name="s1", transport="stdio", command="npx")
    s2 = McpServer(name="s2", transport="stdio", command="npx")
    db.add_all([s1, s2])
    db.commit()
    sync_server_tools(db, s1.id, [tool("search")])
    sync_server_tools(db, s2.id, [tool("search")])
    assert db.query(Tool).filter(Tool.name == "search").count() == 2


def test_deleting_the_server_cascades_to_its_tools(db, server):
    sync_server_tools(db, server, [tool("a"), tool("b")])
    db.delete(db.get(McpServer, server))
    db.commit()
    assert db.query(Tool).count() == 0


def test_empty_tool_list_removes_everything(db, server):
    sync_server_tools(db, server, [tool("a"), tool("b")])
    rows = sync_server_tools(db, server, [])
    assert rows == []
    assert db.query(Tool).filter(Tool.mcp_server_id == server).count() == 0


def test_missing_description_and_schema_default_sensibly(db, server):
    rows = sync_server_tools(db, server, [{"name": "bare"}])
    assert rows[0].description == "" and rows[0].input_schema_json == {} and rows[0].annotations_json == {}
