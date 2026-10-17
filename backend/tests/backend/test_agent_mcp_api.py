"""api/agent_mcp.py end to end: attached servers -> the agent's own config file."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.api import agent_mcp, http
from backend.core.security import get_or_create_access_token
from backend.core.tools import sync_server_tools
from backend.storage import db as storage_db
from backend.storage.db import Agent, McpServer, Tool

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
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    yield


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(http.router, dependencies=[Depends(http.require_token)])
    app.include_router(agent_mcp.router)
    return TestClient(app)


def make_agent(backend="hermes", server_ids=()):
    with storage_db.SessionLocal() as s:
        a = Agent(name="a", agent_backend=backend, config_json={"mcp_server_ids": list(server_ids)})
        s.add(a)
        s.commit()
        return a.id


def make_server(name="Files", env=None, enabled=True, transport="stdio", tools=()):
    with storage_db.SessionLocal() as s:
        row = McpServer(
            name=name, transport=transport, command="npx" if transport == "stdio" else None,
            args_json={"args": ["-y", "srv"]}, env_json=env or {}, enabled=enabled,
            url="http://127.0.0.1:9/mcp" if transport == "http" else None,
        )
        s.add(row)
        s.commit()
        if tools:
            sync_server_tools(s, row.id, [{"name": t, "description": "", "annotations": {}} for t in tools])
        return row.id


def url(aid, tail=""):
    return f"/api/v1/agents/{aid}/mcp{tail}"


@pytest.mark.parametrize("method,tail", [("get", ""), ("post", "/sync"), ("post", "/probe")])
def test_every_route_requires_the_token(client, method, tail):
    assert getattr(client, method)(url(1, tail)).status_code == 401


def test_status_then_sync_writes_the_agents_own_file(client, tmp_path):
    aid = make_agent(server_ids=[make_server()])
    before = client.get(url(aid), headers=AUTH).json()
    assert before["servers"][0]["status"] == "missing" and not (tmp_path / ".hermes" / "config.yaml").exists()
    assert "permission" in before["permission_note"].lower()
    after = client.post(url(aid, "/sync"), headers=AUTH, json={}).json()
    assert after["servers"][0]["status"] == "in_sync" and after["in_sync"] is True
    text = (tmp_path / ".hermes" / "config.yaml").read_text(encoding="utf-8")
    assert "lai_files" in text and "npx" in text


def test_disabled_app_tools_are_excluded_in_the_agents_file(client, tmp_path):
    sid = make_server(tools=["read", "write"])
    with storage_db.SessionLocal() as s:
        s.query(Tool).filter(Tool.name == "write").update({"enabled": False})
        s.commit()
    aid = make_agent(server_ids=[sid])
    client.post(url(aid, "/sync"), headers=AUTH, json={})
    text = (tmp_path / ".hermes" / "config.yaml").read_text(encoding="utf-8")
    assert "exclude" in text and "write" in text and "read" not in text.split("exclude")[1]


def test_secrets_need_confirmation_and_are_never_echoed(client, tmp_path):
    aid = make_agent(server_ids=[make_server("Brave", env={"KEY": "sk-secret-value-1"})])
    r = client.post(url(aid, "/sync"), headers=AUTH, json={}).json()
    assert r["servers"][0]["status"] == "needs_confirm"
    assert not (tmp_path / ".hermes" / "config.yaml").exists()
    r = client.post(url(aid, "/sync"), headers=AUTH, json={"confirm_secrets": True})
    assert "sk-secret-value-1" not in r.text
    assert "sk-secret-value-1" in (tmp_path / ".hermes" / "config.yaml").read_text(encoding="utf-8")


def test_a_disabled_server_is_not_written(client, tmp_path):
    aid = make_agent(server_ids=[make_server(enabled=False)])
    r = client.post(url(aid, "/sync"), headers=AUTH, json={}).json()
    assert r["servers"] == []


def test_detaching_removes_the_entry_on_the_next_sync(client, tmp_path):
    sid = make_server()
    aid = make_agent(server_ids=[sid])
    client.post(url(aid, "/sync"), headers=AUTH, json={})
    with storage_db.SessionLocal() as s:
        a = s.get(Agent, aid)
        a.config_json = {"mcp_server_ids": []}
        s.commit()
    assert client.get(url(aid), headers=AUTH).json()["stale"] == ["lai_files"]
    client.post(url(aid, "/sync"), headers=AUTH, json={})
    assert "lai_files" not in (tmp_path / ".hermes" / "config.yaml").read_text(encoding="utf-8")


def test_open_interpreter_gets_toml(client, tmp_path):
    aid = make_agent("open_interpreter", [make_server(transport="http", name="Web")])
    client.post(url(aid, "/sync"), headers=AUTH, json={})
    text = (tmp_path / ".openinterpreter" / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.lai_web]" in text and "127.0.0.1:9/mcp" in text


def test_generic_agent_and_unknown_backend_are_refused(client):
    assert client.get(url(make_agent("generic")), headers=AUTH).status_code == 400
    assert client.get(url(make_agent("nope")), headers=AUTH).status_code == 400
    assert client.get(url(999), headers=AUTH).status_code == 404


def test_probe_runs_the_agents_own_command_and_redacts_secrets(client, monkeypatch):
    aid = make_agent(server_ids=[make_server("Brave", env={"KEY": "sk-secret-value-1"})])
    monkeypatch.setattr(agent_mcp.agent_backends.get_adapter("hermes"), "mcp_probe_command",
                        lambda key: ["fake", "mcp", "test", key])

    class R:
        returncode, stdout, stderr = 0, "connected with sk-secret-value-1", ""

    seen = {}
    monkeypatch.setattr(agent_mcp.subprocess, "run", lambda cmd, **kw: seen.setdefault("cmd", cmd) and R())
    r = client.post(url(aid, "/probe"), headers=AUTH, json={"server": "Brave"}).json()
    assert seen["cmd"] == ["fake", "mcp", "test", "lai_brave"]
    assert r["ok"] and r["live"] and "sk-secret-value-1" not in r["output"] and "***" in r["output"]
    assert client.post(url(aid, "/probe"), headers=AUTH, json={"server": "zzz"}).status_code == 404
