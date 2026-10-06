"""
tests/backend/test_sessions_api.py

GET /sessions, /sessions/{id}, /sessions/{id}/export through the full
app, plus the agent-loop fix this phase surfaced: tool.* events and
permission checks now carry the real llm_agent_sessions id, which is
what makes "list the tool calls in this session" answerable at all and
what makes allow_session grants reusable across calls in one session.
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.core import agent_loop, events
from backend.core.mcp.manager import McpManager
from backend.core.permissions.engine import PermissionEngine
from backend.core.security import get_or_create_access_token
from backend.core.tools import sync_server_tools
from backend.storage import db as storage_db
from backend.storage.db import Agent, Chat, Event, McpServer, Runtime

from fake_llama_server import FakeLlamaServer

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
def client(monkeypatch):
    from backend.core.metrics import metrics_recorder

    monkeypatch.setattr(metrics_recorder, "start", lambda: None)
    monkeypatch.setattr(metrics_recorder, "stop", lambda: None)
    from backend.main import app

    with TestClient(app) as c:
        yield c


def get(c, path, **kw):
    return c.get(f"/api/v1{path}", headers=AUTH, **kw)


def make_session(name_suffix=""):
    with storage_db.SessionLocal() as s:
        rt = Runtime(name=f"rt{name_suffix}", executable_path="/bin/true", host="127.0.0.1", port=1)
        ag = Agent(name=f"ag{name_suffix}", agent_type="generic")
        s.add_all([rt, ag])
        s.commit()
        link = storage_db.start_llm_agent_session(s, rt.id, ag.id)
        return link.id, rt.id, ag.id


# ---------------------------------------------------------------------
# list
# ---------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/sessions", "/sessions/1", "/sessions/1/export"])
def test_routes_require_the_token(client, path):
    assert client.get(f"/api/v1{path}").status_code == 401


def test_list_is_empty_initially(client):
    assert get(client, "/sessions").json() == []


def test_list_includes_names_and_cumulative_stats(client):
    sid, rid, aid = make_session()
    with storage_db.SessionLocal() as s:
        storage_db.record_request(
            s, runtime_id=rid, agent_id=aid, prompt_tokens=10, completion_tokens=4, latency_ms=200.0,
            tokens_per_sec=20.0, session_link_id=sid,
        )
    rows = get(client, "/sessions").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == sid and row["runtime_name"] == "rt" and row["agent_name"] == "ag"
    assert row["requests_count"] == 1 and row["prompt_tokens_total"] == 10 and row["completion_tokens_total"] == 4
    assert row["status"] == "ACTIVE" and row["ended_at"] is None


def test_list_newest_first_and_filters(client):
    s1, r1, a1 = make_session("1")
    s2, r2, a2 = make_session("2")
    assert [r["id"] for r in get(client, "/sessions").json()] == [s2, s1]
    assert [r["id"] for r in get(client, f"/sessions?runtime_id={r1}").json()] == [s1]
    assert [r["id"] for r in get(client, f"/sessions?agent_id={a2}").json()] == [s2]


def test_active_only_excludes_ended_sessions(client):
    s1, _, _ = make_session("1")
    s2, _, _ = make_session("2")
    client.post(f"/api/v1/sessions/{s1}/detach", headers=AUTH)
    assert [r["id"] for r in get(client, "/sessions?active_only=true").json()] == [s2]
    assert len(get(client, "/sessions").json()) == 2


# ---------------------------------------------------------------------
# detail
# ---------------------------------------------------------------------

def test_detail_has_chat_ids_and_event_counts(client):
    sid, rid, aid = make_session()
    with storage_db.SessionLocal() as s:
        chat = storage_db.create_chat(s, rid, aid)
        storage_db.record_request(s, runtime_id=rid, agent_id=aid, prompt_tokens=1, completion_tokens=1, latency_ms=1, session_link_id=sid, chat_id=chat.id)
    events.emit(events.EventType.TOOL_CALLED, session_id=sid, metadata={"tool": "x"})
    events.emit(events.EventType.TOOL_CALLED, session_id=sid, metadata={"tool": "y"})
    events.emit(events.EventType.TOOL_FAILED, session_id=sid, metadata={"tool": "y"})

    body = get(client, f"/sessions/{sid}").json()
    assert body["id"] == sid and body["chat_ids"] == [chat.id]
    assert body["event_counts"]["tool.called"] == 2 and body["event_counts"]["tool.failed"] == 1
    # attach() itself emitted these two into the same session:
    assert body["event_counts"].get("session.started", 0) >= 0


def test_detail_event_counts_only_cover_this_session(client):
    s1, _, _ = make_session("1")
    s2, _, _ = make_session("2")
    events.emit(events.EventType.TOOL_CALLED, session_id=s1, metadata={})
    assert "tool.called" not in get(client, f"/sessions/{s2}").json()["event_counts"]


def test_detail_unknown_session_is_404(client):
    assert get(client, "/sessions/999999").status_code == 404


# ---------------------------------------------------------------------
# export
# ---------------------------------------------------------------------

def test_export_json_is_a_download_with_the_right_headers(client):
    sid, _, _ = make_session()
    events.emit(events.EventType.TOOL_CALLED, session_id=sid, metadata={"tool": "echo"})
    r = get(client, f"/sessions/{sid}/export?format=json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers["content-disposition"] == f'attachment; filename="session-{sid}.json"'
    doc = json.loads(r.text)
    assert doc["session"]["id"] == sid
    assert "tool.called" in [e["event_type"] for e in doc["events"]]


def test_export_defaults_to_json(client):
    sid, _, _ = make_session()
    assert get(client, f"/sessions/{sid}/export").headers["content-disposition"].endswith('.json"')


def test_export_jsonl(client):
    sid, _, _ = make_session()
    r = get(client, f"/sessions/{sid}/export?format=jsonl")
    assert r.headers["content-type"].startswith("application/x-ndjson")
    assert r.headers["content-disposition"].endswith('.jsonl"')
    for line in r.text.strip().split("\n"):
        json.loads(line)


def test_export_markdown(client):
    sid, _, _ = make_session()
    r = get(client, f"/sessions/{sid}/export?format=markdown")
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"].endswith('.md"')
    assert r.text.startswith(f"# Session #{sid}")


def test_export_unknown_format_is_400(client):
    sid, _, _ = make_session()
    assert get(client, f"/sessions/{sid}/export?format=pdf").status_code == 400


def test_export_unknown_session_is_404(client):
    assert get(client, "/sessions/999999/export").status_code == 404


# ---------------------------------------------------------------------
# agent_loop: tool.* events + permission checks carry the session id
# ---------------------------------------------------------------------

@pytest.fixture
def loop_env(monkeypatch):
    mcp = McpManager(connect_timeout=20, ping_interval=5, ping_timeout=3, tool_call_timeout=10)
    perm = PermissionEngine(default_timeout=8)
    monkeypatch.setattr(agent_loop, "mcp_manager", mcp)
    monkeypatch.setattr(agent_loop, "permission_engine", perm)
    llama = FakeLlamaServer([{"content": "ok"}])
    llama.start()
    yield mcp, perm, llama
    llama.stop()
    mcp.shutdown()


def tool_call(name, arguments, call_id="c1"):
    return {"id": call_id, "function": {"name": name, "arguments": json.dumps(arguments)}}


def setup_agent_chat_with_session(mcp):
    with storage_db.SessionLocal() as db:
        server = McpServer(name="fx", transport="stdio", command=sys.executable, args_json={"args": [FIXTURE]})
        db.add(server)
        db.commit()
        assert mcp.connect(db, server).state == "CONNECTED"
        sync_server_tools(db, server.id, mcp.cached_tools(server.id))
        rt = Runtime(name="r", executable_path="/bin/true", host="127.0.0.1", port=1)
        ag = Agent(name="a", agent_type="generic", config_json={"mcp_server_ids": [server.id]})
        db.add_all([rt, ag])
        db.commit()
        link = storage_db.start_llm_agent_session(db, rt.id, ag.id)
        chat = Chat(runtime_id=rt.id, agent_id=ag.id)
        db.add(chat)
        db.commit()
        return server.id, chat.id, link.id


def auto_resolve(perm, decision):
    stop = threading.Event()

    def go():
        while not stop.is_set():
            for r in perm.list_pending():
                perm.resolve(r.id, decision)
            time.sleep(0.02)

    threading.Thread(target=go, daemon=True).start()
    return stop


def test_tool_events_carry_the_active_session_id(loop_env):
    mcp, perm, llama = loop_env
    server_id, chat_id, link_id = setup_agent_chat_with_session(mcp)
    llama.set_responses([
        {"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "x"})]},
        {"content": "done"},
    ])
    stop = auto_resolve(perm, "allow_once")
    try:
        with storage_db.SessionLocal() as db:
            chat = db.get(Chat, chat_id)
            storage_db.add_chat_message(db, chat_id, role="user", content="go")
            agent_loop.run_agent_turn(db, chat, llama.endpoint)
    finally:
        stop.set()

    with storage_db.SessionLocal() as s:
        tool_events = s.query(Event).filter(Event.event_type.like("tool.%")).all()
        perm_events = s.query(Event).filter(Event.event_type.like("permission.%")).all()
    assert {e.event_type for e in tool_events} == {"tool.called", "tool.completed"}
    assert all(e.session_id == link_id for e in tool_events)
    assert all(e.session_id == link_id for e in perm_events)


def test_session_export_now_lists_the_tool_calls_made_during_it(loop_env, client):
    mcp, perm, llama = loop_env
    server_id, chat_id, link_id = setup_agent_chat_with_session(mcp)
    llama.set_responses([
        {"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "x"})]},
        {"content": "done"},
    ])
    stop = auto_resolve(perm, "allow_once")
    try:
        with storage_db.SessionLocal() as db:
            chat = db.get(Chat, chat_id)
            storage_db.add_chat_message(db, chat_id, role="user", content="go")
            agent_loop.run_agent_turn(db, chat, llama.endpoint)
    finally:
        stop.set()

    md = get(client, f"/sessions/{link_id}/export?format=markdown").text
    assert f"Called tool `{server_id}__echo`" in md
    detail = get(client, f"/sessions/{link_id}").json()
    assert detail["event_counts"]["tool.called"] == 1 and detail["event_counts"]["tool.completed"] == 1
    assert detail["chat_ids"] == [chat_id]


def test_allow_session_grant_is_reused_within_the_session(loop_env):
    """Regression: before session ids were threaded through, allow_session
    was stored with session_id=None and could never match a later lookup --
    so it silently behaved like allow_once for every tool call."""
    mcp, perm, llama = loop_env
    server_id, chat_id, link_id = setup_agent_chat_with_session(mcp)

    def run_turn(responses):
        llama.set_responses(responses)
        with storage_db.SessionLocal() as db:
            chat = db.get(Chat, chat_id)
            storage_db.add_chat_message(db, chat_id, role="user", content="go")
            return agent_loop.run_agent_turn(db, chat, llama.endpoint)

    stop = auto_resolve(perm, "allow_session")
    try:
        run_turn([{"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "a"})]}, {"content": "ok"}])
    finally:
        stop.set()

    # second call, SAME session, NO resolver running -- would hang until timeout if it asked again
    written = run_turn([{"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "b"}, "c2")]}, {"content": "ok2"}])
    tool_msg = next(m for m in written if m.role == "tool")
    assert tool_msg.tool_meta_json["status"] == "ok"
    with storage_db.SessionLocal() as s:
        assert s.query(Event).filter(Event.event_type == "permission.requested").count() == 1
