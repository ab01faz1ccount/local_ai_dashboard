"""
tests/backend/test_agent_loop.py

core/agent_loop.py end to end: a real MCP fixture server
(mcp_fixture_server.py) for tools, a real PermissionEngine (resolved
from a background thread, same shape as its own tests) for gating, and
a real HTTP server (fake_llama_server.py) standing in for llama-server.
Nothing here is mocked at the boundary that matters -- only the model's
own responses are scripted, since there is no real model in a test run.
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from backend.core import agent_loop, chat as chat_engine
from backend.core.mcp.manager import McpManager
from backend.core.permissions.engine import PermissionEngine
from backend.core.tools import sync_server_tools
from backend.storage import db as storage_db
from backend.storage.db import Agent, Chat, ChatMessage, Event, McpServer, Runtime

from fake_llama_server import FakeLlamaServer

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
def db():
    s = storage_db.SessionLocal()
    yield s
    s.close()


@pytest.fixture
def mcp(monkeypatch):
    m = McpManager(connect_timeout=20, ping_interval=5, ping_timeout=3, tool_call_timeout=10)
    monkeypatch.setattr(agent_loop, "mcp_manager", m)
    yield m
    m.shutdown()


@pytest.fixture
def perm(monkeypatch):
    p = PermissionEngine(default_timeout=8)
    monkeypatch.setattr(agent_loop, "permission_engine", p)
    return p


@pytest.fixture
def llama():
    s = FakeLlamaServer([{"content": "hi"}])
    s.start()
    yield s
    s.stop()


def make_chat_with_agent(db, mcp_server_ids, agent_config=None):
    runtime = Runtime(name="r", executable_path="/bin/true", host="127.0.0.1", port=1)
    agent = Agent(name="a", agent_type="generic", config_json={"mcp_server_ids": mcp_server_ids, **(agent_config or {})})
    db.add_all([runtime, agent])
    db.commit()
    chat = Chat(runtime_id=runtime.id, agent_id=agent.id)
    db.add(chat)
    db.commit()
    return chat


def connect_fixture_server(db, mcp, name="fx", extra_args=()) -> int:
    row = McpServer(name=name, transport="stdio", command=sys.executable, args_json={"args": [FIXTURE, *extra_args]})
    db.add(row)
    db.commit()
    status = mcp.connect(db, row)
    assert status.state == "CONNECTED", status.error
    sync_server_tools(db, row.id, mcp.cached_tools(row.id))
    return row.id


def auto_allow(perm, decision="allow_always"):
    """Approves every pending permission request as it appears, in a
    background thread, so a test can just call run_agent_turn and not
    hand-roll a resolver for each tool step."""
    stop = threading.Event()

    def go():
        while not stop.is_set():
            for r in perm.list_pending():
                perm.resolve(r.id, decision)
            time.sleep(0.02)

    t = threading.Thread(target=go, daemon=True)
    t.start()
    return stop


def messages(db, chat_id):
    return storage_db.get_chat_messages(db, chat_id)


def tool_call(name, arguments, call_id="call_1"):
    """Wire-format tool call, exactly as llama-server's JSON response
    carries it (function.arguments is a JSON-encoded STRING) -- this is
    what a FakeLlamaServer response's "tool_calls" list should contain;
    core.chat._parse_tool_calls is what turns it into the simpler
    {"id","name","arguments"} shape agent_loop operates on."""
    return {"id": call_id, "function": {"name": name, "arguments": json.dumps(arguments)}}


def event_types(prefix=""):
    with storage_db.SessionLocal() as s:
        return [e.event_type for e in s.query(Event).order_by(Event.id) if e.event_type.startswith(prefix)]


# ---------------------------------------------------------------------
# no tools attached / no tool call made
# ---------------------------------------------------------------------

def test_plain_turn_with_no_agent_is_a_single_completion(db, llama):
    runtime = Runtime(name="r", executable_path="/bin/true", host="127.0.0.1", port=1)
    db.add(runtime)
    db.commit()
    chat = Chat(runtime_id=runtime.id, agent_id=None)
    db.add(chat)
    db.commit()
    storage_db.add_chat_message(db, chat.id, role="user", content="hello")

    written = agent_loop.run_agent_turn(db, chat, llama.endpoint)

    assert len(written) == 1 and written[0].role == "assistant" and written[0].content == "hi"
    assert "tools" not in llama.requests[0]
    assert event_types("inference.") == ["inference.started", "inference.completed"]


def test_agent_with_no_mcp_servers_configured_gets_no_tools(db, llama, mcp):
    chat = make_chat_with_agent(db, mcp_server_ids=[])
    storage_db.add_chat_message(db, chat.id, role="user", content="hi")
    agent_loop.run_agent_turn(db, chat, llama.endpoint)
    assert "tools" not in llama.requests[0]


def test_model_response_without_tool_calls_ends_the_turn(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="hi")
    llama.responses = [{"content": "no tools needed"}]

    written = agent_loop.run_agent_turn(db, chat, llama.endpoint)

    assert len(written) == 1 and written[0].content == "no tools needed"
    sent_tools = {t["function"]["name"] for t in llama.requests[0]["tools"]}
    assert f"{server_id}__echo" in sent_tools  # offered, just not called


# ---------------------------------------------------------------------
# a full tool round trip
# ---------------------------------------------------------------------

def test_one_tool_call_round_trip(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="echo hi")
    llama.responses = [
        {"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "hello"})]},
        {"content": "the tool said hello"},
    ]
    stop = auto_allow(perm)
    try:
        written = agent_loop.run_agent_turn(db, chat, llama.endpoint)
    finally:
        stop.set()

    roles = [m.role for m in written]
    assert roles == ["assistant", "tool", "assistant"]
    assert written[1].content == "hello"
    assert written[1].tool_meta_json["status"] == "ok"
    assert written[1].tool_meta_json["name"] == f"{server_id}__echo"
    assert written[2].content == "the tool said hello"
    # the model's second call saw the tool's result fed back
    second_request_messages = llama.requests[1]["messages"]
    assert second_request_messages[-1]["role"] == "tool" and second_request_messages[-1]["content"] == "hello"
    assert event_types("tool.") == ["tool.called", "tool.completed"]


def test_tool_error_is_fed_back_as_the_tool_message_not_a_crash(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="do it")
    llama.responses = [
        {"content": "", "tool_calls": [tool_call(f"{server_id}__fail", {"message": "nope"})]},
        {"content": "sorry, that failed"},
    ]
    stop = auto_allow(perm)
    try:
        written = agent_loop.run_agent_turn(db, chat, llama.endpoint)
    finally:
        stop.set()

    assert written[1].role == "tool" and written[1].tool_meta_json["status"] == "error"
    assert "nope" in written[1].content
    assert written[2].content == "sorry, that failed"
    assert event_types("tool.") == ["tool.called", "tool.failed"]


def test_multiple_tool_calls_in_one_step_all_run(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="do both")
    llama.responses = [
        {"content": "", "tool_calls": [
            tool_call(f"{server_id}__echo", {"text": "a"}, "c1"),
            tool_call(f"{server_id}__add", {"a": 1, "b": 2}, "c2"),
        ]},
        {"content": "done"},
    ]
    stop = auto_allow(perm)
    try:
        written = agent_loop.run_agent_turn(db, chat, llama.endpoint)
    finally:
        stop.set()

    tool_msgs = [m for m in written if m.role == "tool"]
    assert len(tool_msgs) == 2
    assert {m.tool_meta_json["call_id"] for m in tool_msgs} == {"c1", "c2"}
    assert {m.content for m in tool_msgs} == {"a", "3"}


# ---------------------------------------------------------------------
# permission gating
# ---------------------------------------------------------------------

def test_denied_permission_produces_a_denied_tool_message_not_a_call(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="delete it")
    llama.responses = [
        {"content": "", "tool_calls": [tool_call(f"{server_id}__delete_file", {"path": "/etc/passwd"})]},
        {"content": "okay, not doing that"},
    ]
    stop = auto_allow(perm, decision="deny")
    try:
        written = agent_loop.run_agent_turn(db, chat, llama.endpoint)
    finally:
        stop.set()

    tool_msg = next(m for m in written if m.role == "tool")
    assert tool_msg.tool_meta_json["status"] == "denied" and tool_msg.content == "permission denied"
    assert event_types("tool.") == ["tool.called", "tool.failed"]
    assert event_types("permission.")[:2] == ["permission.requested", "permission.denied"]


def test_destructive_tool_is_requested_at_critical_risk(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="go")
    llama.responses = [
        {"content": "", "tool_calls": [tool_call(f"{server_id}__delete_file", {"path": "/tmp/x"})]},
        {"content": "done"},
    ]

    seen = {}

    def resolver():
        end = time.time() + 5
        while time.time() < end:
            pend = perm.list_pending()
            if pend:
                seen["risk"] = pend[0].risk_level
                perm.resolve(pend[0].id, "allow_once")
                return
            time.sleep(0.02)

    t = threading.Thread(target=resolver)
    t.start()
    agent_loop.run_agent_turn(db, chat, llama.endpoint)
    t.join(5)
    assert seen["risk"] == "CRITICAL"


def test_standing_grant_from_an_earlier_call_skips_asking_again(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])

    # first turn: allow_always
    storage_db.add_chat_message(db, chat.id, role="user", content="echo a")
    llama.responses = [{"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "a"})]}, {"content": "ok"}]
    stop = auto_allow(perm, decision="allow_always")
    try:
        agent_loop.run_agent_turn(db, chat, llama.endpoint)
    finally:
        stop.set()
    assert event_types("permission.requested") == ["permission.requested"]

    # second turn, same tool: must NOT create a new pending request
    storage_db.add_chat_message(db, chat.id, role="user", content="echo b")
    llama.set_responses([{"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "b"})]}, {"content": "ok again"}])
    written = agent_loop.run_agent_turn(db, chat, llama.endpoint)  # NO resolver running -- would hang if it asked again
    assert [m.role for m in written] == ["assistant", "tool", "assistant"]
    assert event_types("permission.requested") == ["permission.requested"]  # still just the one, ever


# ---------------------------------------------------------------------
# unknown / disconnected tools, and tools not attached to the agent
# ---------------------------------------------------------------------

def test_unknown_tool_name_is_refused_without_a_permission_prompt(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="hi")
    llama.responses = [{"content": "", "tool_calls": [tool_call("99999__nonexistent", {})]}, {"content": "hmm"}]

    written = agent_loop.run_agent_turn(db, chat, llama.endpoint)  # no resolver -- must never wait on a human

    tool_msg = next(m for m in written if m.role == "tool")
    assert "unknown tool" in tool_msg.content
    assert perm.list_pending() == []
    assert event_types("permission.") == []


def test_a_tool_not_attached_to_this_agent_is_unknown_even_if_another_server_has_it(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp, name="attached")
    other_id = connect_fixture_server(db, mcp, name="not-attached")
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])  # only "attached"
    storage_db.add_chat_message(db, chat.id, role="user", content="hi")
    llama.responses = [{"content": "", "tool_calls": [tool_call(f"{other_id}__echo", {"text": "x"})]}, {"content": "ok"}]

    written = agent_loop.run_agent_turn(db, chat, llama.endpoint)
    assert "unknown tool" in next(m for m in written if m.role == "tool").content


def test_disabled_tool_is_not_offered_and_a_call_to_it_is_unknown(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    from backend.storage.db import Tool

    row = db.query(Tool).filter(Tool.mcp_server_id == server_id, Tool.name == "echo").one()
    row.enabled = False
    db.commit()

    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="hi")
    llama.responses = [{"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "x"})]}, {"content": "ok"}]

    written = agent_loop.run_agent_turn(db, chat, llama.endpoint)
    assert f"{server_id}__echo" not in {t["function"]["name"] for t in llama.requests[0]["tools"]}
    assert "unknown tool" in next(m for m in written if m.role == "tool").content


def test_disconnected_server_tool_is_reported_without_a_permission_prompt(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    with storage_db.SessionLocal() as s:
        row = s.get(McpServer, server_id)
        mcp.disconnect(s, row)

    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="hi")
    # the tool is no longer OFFERED at all once its server is disconnected...
    agent_loop.run_agent_turn(db, chat, llama.endpoint)
    assert "tools" not in llama.requests[0]


# ---------------------------------------------------------------------
# max_steps
# ---------------------------------------------------------------------

def test_a_model_that_always_asks_for_a_tool_eventually_stops(db, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="loop forever")

    def always_call(_body):
        return {"content": "", "tool_calls": [{"id": "c", "function": {"name": f"{server_id}__echo", "arguments": '{"text":"x"}'}}]}

    server = FakeLlamaServer([always_call])
    server.start()
    stop = auto_allow(perm)
    try:
        written = agent_loop.run_agent_turn(db, chat, server.endpoint, max_steps=3)
    finally:
        stop.set()
        server.stop()

    executed = [m for m in written if m.role == "tool" and m.tool_meta_json.get("status") == "ok"]
    assert len(executed) <= 3
    assert len(server.requests) < 100  # actually terminated, not an infinite loop


def test_max_steps_of_zero_still_makes_one_completion_call_but_never_a_tool(db, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="hi")
    server = FakeLlamaServer([{"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "x"})]}])
    server.start()
    try:
        written = agent_loop.run_agent_turn(db, chat, server.endpoint, max_steps=0)
    finally:
        server.stop()
    assert written[0].role == "assistant"
    assert all(m.tool_meta_json.get("status") != "ok" for m in written if m.role == "tool")
    assert perm.list_pending() == []


# ---------------------------------------------------------------------
# failure paths
# ---------------------------------------------------------------------

def test_completion_failure_on_the_first_call_raises_and_writes_nothing(db, mcp, perm):
    chat = make_chat_with_agent(db, mcp_server_ids=[])
    storage_db.add_chat_message(db, chat.id, role="user", content="hi")
    with pytest.raises(chat_engine.ChatCompletionError):
        agent_loop.run_agent_turn(db, chat, "http://127.0.0.1:1", permission_timeout=1)
    assert [m.role for m in messages(db, chat.id)] == ["user"]
    assert event_types("inference.") == ["inference.started", "inference.failed"]


def test_completion_failure_after_a_tool_step_keeps_the_tool_message(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="hi")
    llama.responses = [
        {"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "x"})]},
        {"status": 500, "body": "boom"},
    ]
    stop = auto_allow(perm)
    try:
        with pytest.raises(chat_engine.ChatCompletionError):
            agent_loop.run_agent_turn(db, chat, llama.endpoint)
    finally:
        stop.set()
    roles = [m.role for m in messages(db, chat.id)]
    assert roles == ["user", "assistant", "tool"]  # the tool step survived the later failure


def test_call_that_raises_inside_mcp_manager_becomes_an_error_tool_message(db, llama, mcp, perm, monkeypatch):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="hi")
    llama.responses = [{"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "x"})]}, {"content": "ok"}]

    def boom(*a, **kw):
        raise RuntimeError("transport died")

    monkeypatch.setattr(mcp, "call_tool", boom)
    stop = auto_allow(perm)
    try:
        written = agent_loop.run_agent_turn(db, chat, llama.endpoint)
    finally:
        stop.set()
    tool_msg = next(m for m in written if m.role == "tool")
    assert tool_msg.tool_meta_json["status"] == "error" and "transport died" in tool_msg.content


# ---------------------------------------------------------------------
# history shape sent to the model
# ---------------------------------------------------------------------

def test_tool_calls_and_results_are_correctly_threaded_into_history(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="echo hi")
    llama.responses = [
        {"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "hi"}, "abc")]},
        {"content": "done"},
    ]
    stop = auto_allow(perm)
    try:
        agent_loop.run_agent_turn(db, chat, llama.endpoint)
    finally:
        stop.set()

    second = llama.requests[1]["messages"]
    assistant_step = next(m for m in second if m.get("role") == "assistant" and m.get("tool_calls"))
    assert assistant_step["tool_calls"][0]["id"] == "abc"
    assert assistant_step["tool_calls"][0]["function"]["name"] == f"{server_id}__echo"
    tool_step = next(m for m in second if m.get("role") == "tool")
    assert tool_step["tool_call_id"] == "abc" and tool_step["content"] == "hi"


# ---------------------------------------------------------------------
# request logging (usage stats)
# ---------------------------------------------------------------------

def test_each_completion_call_in_a_turn_is_logged_separately(db, llama, mcp, perm):
    from backend.storage.db import RequestLog

    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    storage_db.add_chat_message(db, chat.id, role="user", content="hi")
    llama.responses = [
        {"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "x"})], "prompt_tokens": 10, "completion_tokens": 5},
        {"content": "ok", "prompt_tokens": 20, "completion_tokens": 8},
    ]
    stop = auto_allow(perm)
    try:
        agent_loop.run_agent_turn(db, chat, llama.endpoint)
    finally:
        stop.set()
    with storage_db.SessionLocal() as s:
        logs = s.query(RequestLog).filter(RequestLog.chat_id == chat.id).order_by(RequestLog.id).all()
    assert len(logs) == 2
    assert [l.prompt_tokens for l in logs] == [10, 20]
