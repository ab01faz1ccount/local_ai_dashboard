"""
tests/backend/test_context_inspector.py

core/context_inspector.py: token estimation, real /tokenize (against
fake_llama_server's genuine HTTP endpoint), window resolution, pressure
bands, the per-category breakdown over a real chat, tool-definition
accounting against what the agent loop actually sends, and the API route.
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.core import agent_loop, context_inspector as ci
from backend.core.engine.base import EngineStatus, RuntimeState
from backend.core.mcp.manager import McpManager
from backend.core.security import get_or_create_access_token
from backend.core.tools import sync_server_tools
from backend.storage import db as storage_db
from backend.storage.db import Agent, Chat, MLModel, McpServer, Runtime

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
def db():
    s = storage_db.SessionLocal()
    yield s
    s.close()


@pytest.fixture
def server():
    s = FakeLlamaServer([{"content": "ok"}])
    s.start()
    yield s
    s.stop()


def make_chat(db, *, ctx_size=None, model_ctx=None, agent=False, agent_config=None):
    model = None
    if model_ctx is not None:
        model = MLModel(name="m", file_path="/m.gguf", file_size_bytes=1, context_length=model_ctx)
        db.add(model)
        db.commit()
    cfg = {"ctx_size": ctx_size} if ctx_size else {}
    rt = Runtime(name="r", executable_path="/bin/true", host="127.0.0.1", port=1, config_json=cfg, model_id=model.id if model else None)
    db.add(rt)
    db.commit()
    agent_id = None
    if agent:
        ag = Agent(name="a", agent_type="generic", config_json=agent_config or {})
        db.add(ag)
        db.commit()
        agent_id = ag.id
    chat = Chat(runtime_id=rt.id, agent_id=agent_id)
    db.add(chat)
    db.commit()
    return chat


def msg(db, chat, role, content, **kw):
    return storage_db.add_chat_message(db, chat.id, role=role, content=content, **kw)


def cat(result, key):
    return next(c for c in result["categories"] if c["key"] == key)


# ---------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------

def test_estimate_empty_is_zero():
    assert ci.estimate_tokens("") == 0


def test_estimate_ascii_is_about_four_chars_per_token():
    assert ci.estimate_tokens("a" * 400) == 100


def test_estimate_non_ascii_costs_more_per_char():
    assert ci.estimate_tokens("س" * 150) == 100  # 1.5 chars/token
    assert ci.estimate_tokens("س" * 100) > ci.estimate_tokens("a" * 100)


def test_estimate_mixed_text_adds_both():
    assert ci.estimate_tokens("a" * 40 + "س" * 15) == 10 + 10


def test_estimate_rounds_up():
    assert ci.estimate_tokens("abc") == 1


# ---------------------------------------------------------------------
# tokenize_count (real HTTP)
# ---------------------------------------------------------------------

def test_tokenize_count_uses_the_servers_answer(server):
    assert ci.tokenize_count(server.endpoint, "one two three") == 3
    assert server.tokenize_requests == [{"content": "one two three"}]


def test_tokenize_empty_text_skips_the_network(server):
    assert ci.tokenize_count(server.endpoint, "") == 0
    assert server.tokenize_requests == []


def test_tokenize_unreachable_raises():
    with pytest.raises(Exception):
        ci.tokenize_count("http://127.0.0.1:1", "x")


# ---------------------------------------------------------------------
# window + pressure
# ---------------------------------------------------------------------

def test_runtime_ctx_size_wins_over_model_metadata(db):
    chat = make_chat(db, ctx_size=4096, model_ctx=32768)
    rt = db.get(Runtime, chat.runtime_id)
    assert ci.resolve_context_window(rt, db.get(MLModel, rt.model_id)) == (4096, "runtime_config")


def test_falls_back_to_model_context_length(db):
    chat = make_chat(db, model_ctx=8192)
    rt = db.get(Runtime, chat.runtime_id)
    assert ci.resolve_context_window(rt, db.get(MLModel, rt.model_id)) == (8192, "model_metadata")


def test_no_window_known(db):
    chat = make_chat(db)
    assert ci.resolve_context_window(db.get(Runtime, chat.runtime_id), None) == (None, None)


@pytest.mark.parametrize("bad", [0, -1, "4096", None])
def test_nonsense_ctx_size_is_ignored(db, bad):
    chat = make_chat(db, model_ctx=2048)
    rt = db.get(Runtime, chat.runtime_id)
    rt.config_json = {"ctx_size": bad}
    assert ci.resolve_context_window(rt, db.get(MLModel, rt.model_id)) == (2048, "model_metadata")


@pytest.mark.parametrize(
    "total,expected",
    [(0, "OK"), (7499, "OK"), (7500, "HIGH"), (8999, "HIGH"), (9000, "CRITICAL"), (10000, "CRITICAL"), (10001, "OVER")],
)
def test_pressure_bands_for_a_10k_window(total, expected):
    assert ci.pressure_status(total, 10000) == expected


def test_pressure_unknown_without_a_window():
    assert ci.pressure_status(500, None) == "UNKNOWN"
    assert ci.pressure_status(500, 0) == "UNKNOWN"


# ---------------------------------------------------------------------
# inspect_chat_context
# ---------------------------------------------------------------------

def test_empty_chat(db):
    chat = make_chat(db, ctx_size=4096)
    r = ci.inspect_chat_context(db, chat)
    assert r["total_tokens"] == 0 and r["status"] == "OK" and r["largest_items"] == []
    assert r["last_prompt_tokens"] is None and r["tool_count"] == 0
    assert [c["key"] for c in r["categories"]] == ["system", "tool_definitions", "conversation", "tool_results"]


def test_messages_land_in_the_right_categories(db):
    chat = make_chat(db, ctx_size=1000)
    msg(db, chat, "system", "a" * 40)
    msg(db, chat, "user", "b" * 80)
    msg(db, chat, "assistant", "c" * 40)
    msg(db, chat, "tool", "d" * 400)
    r = ci.inspect_chat_context(db, chat)
    assert cat(r, "system")["tokens"] == 10 and cat(r, "system")["items"] == 1
    # a category's texts are joined with "\n" before counting (as a template
    # would separate them), so the separator is part of the estimate
    assert cat(r, "conversation")["tokens"] == ci.estimate_tokens("b" * 80 + "\n" + "c" * 40) == 31
    assert cat(r, "conversation")["items"] == 2
    assert cat(r, "tool_results")["tokens"] == 100 and cat(r, "tool_results")["items"] == 1
    assert cat(r, "tool_definitions")["tokens"] == 0
    assert r["total_tokens"] == 10 + 31 + 100
    assert r["method"] == "estimate"


def test_percentages_and_headroom_against_the_window(db):
    chat = make_chat(db, ctx_size=200)
    msg(db, chat, "user", "x" * 400)  # 100 tokens
    r = ci.inspect_chat_context(db, chat)
    assert r["context_window"] == 200 and r["context_window_source"] == "runtime_config"
    assert r["percent_used"] == 50.0 and r["headroom_tokens"] == 100
    assert cat(r, "conversation")["percent_of_window"] == 50.0
    assert r["status"] == "OK"


def test_over_the_window_has_negative_headroom(db):
    chat = make_chat(db, ctx_size=50)
    msg(db, chat, "user", "x" * 400)
    r = ci.inspect_chat_context(db, chat)
    assert r["status"] == "OVER" and r["headroom_tokens"] == -50


def test_unknown_window_leaves_percentages_null(db):
    chat = make_chat(db)
    msg(db, chat, "user", "hello")
    r = ci.inspect_chat_context(db, chat)
    assert r["context_window"] is None and r["percent_used"] is None and r["headroom_tokens"] is None
    assert r["status"] == "UNKNOWN" and cat(r, "conversation")["percent_of_window"] is None


def test_assistant_tool_calls_count_toward_the_conversation(db):
    chat = make_chat(db, ctx_size=1000)
    msg(db, chat, "assistant", "", tool_meta={"calls": [{"id": "c1", "name": "1__echo", "arguments": {"text": "hello world"}}]})
    wire = agent_loop.history_for_completion(db, chat.id)[0]["tool_calls"]
    expected = ci.estimate_tokens("\n" + json.dumps(wire, ensure_ascii=False))
    got = cat(ci.inspect_chat_context(db, chat), "conversation")["tokens"]
    assert got == expected and got > 10  # the call JSON itself, not just a joiner character


def test_largest_items_are_ranked_by_size_with_previews(db):
    chat = make_chat(db, ctx_size=10000)
    small = msg(db, chat, "user", "hi")
    big = msg(db, chat, "tool", "line one\nline two " + "z" * 500)
    mid = msg(db, chat, "assistant", "m" * 100)
    r = ci.inspect_chat_context(db, chat, top_n=2)
    assert [i["message_id"] for i in r["largest_items"]] == [big.id, mid.id]
    top = r["largest_items"][0]
    assert top["role"] == "tool" and top["category"] == "tool_results" and top["chars"] > 500
    assert top["preview"].startswith("line one line two") and top["preview"].endswith("…") and "\n" not in top["preview"]


def test_last_prompt_tokens_comes_from_the_latest_assistant_reply(db):
    chat = make_chat(db, ctx_size=4096)
    msg(db, chat, "user", "q1")
    msg(db, chat, "assistant", "a1", prompt_tokens=111)
    msg(db, chat, "user", "q2")
    msg(db, chat, "assistant", "a2", prompt_tokens=222)
    msg(db, chat, "user", "q3")  # pending, unanswered
    assert ci.inspect_chat_context(db, chat)["last_prompt_tokens"] == 222


def test_persian_text_is_not_underestimated_like_ascii(db):
    chat = make_chat(db, ctx_size=4096)
    msg(db, chat, "user", "س" * 300)
    assert cat(ci.inspect_chat_context(db, chat), "conversation")["tokens"] == 200


# ---------------------------------------------------------------------
# tokenizer vs estimate
# ---------------------------------------------------------------------

def test_online_runtime_uses_the_real_tokenizer_one_call_per_category(db, server):
    chat = make_chat(db, ctx_size=4096)
    msg(db, chat, "system", "you are helpful")        # 3 words
    msg(db, chat, "user", "one two")                  # 2
    msg(db, chat, "assistant", "three")               # 1
    msg(db, chat, "tool", "a b c d")                  # 4
    r = ci.inspect_chat_context(db, chat, endpoint=server.endpoint)
    assert r["method"] == "tokenizer"
    assert cat(r, "system")["tokens"] == 3
    assert cat(r, "conversation")["tokens"] == 3  # "one two\nthree" -> 3 words
    assert cat(r, "tool_results")["tokens"] == 4
    assert cat(r, "tool_definitions")["tokens"] == 0
    # batched per category (not per message), and the empty category made no call
    assert len(server.tokenize_requests) == 3


def test_tokenizer_failure_falls_back_to_estimates_for_every_category(db):
    chat = make_chat(db, ctx_size=4096)
    msg(db, chat, "system", "a" * 40)
    msg(db, chat, "user", "b" * 40)
    r = ci.inspect_chat_context(db, chat, endpoint="http://127.0.0.1:1")
    assert r["method"] == "estimate"
    assert cat(r, "system")["tokens"] == 10 and cat(r, "conversation")["tokens"] == 10


def test_a_tokenizer_that_dies_midway_never_mixes_methods(db):
    chat = make_chat(db, ctx_size=4096)
    msg(db, chat, "system", "a" * 40)
    msg(db, chat, "user", "b " * 20)
    calls = {"n": 0}

    def flaky(text):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("tokenizer died")
        return 7

    s = FakeLlamaServer([{"content": "x"}], tokenize=flaky)
    s.start()
    try:
        r = ci.inspect_chat_context(db, chat, endpoint=s.endpoint)
    finally:
        s.stop()
    assert r["method"] == "estimate"
    assert cat(r, "system")["tokens"] == 10  # estimated, NOT the 7 the first call returned


# ---------------------------------------------------------------------
# tool definitions == what the agent loop really sends
# ---------------------------------------------------------------------

def test_tool_definitions_match_what_the_loop_actually_sends(db, monkeypatch):
    mcp = McpManager(connect_timeout=20, ping_interval=5, ping_timeout=3, tool_call_timeout=10)
    monkeypatch.setattr(agent_loop, "mcp_manager", mcp)
    llama = FakeLlamaServer([{"content": "ok"}])
    llama.start()
    try:
        server_row = McpServer(name="fx", transport="stdio", command=sys.executable, args_json={"args": [FIXTURE]})
        db.add(server_row)
        db.commit()
        assert mcp.connect(db, server_row).state == "CONNECTED"
        sync_server_tools(db, server_row.id, mcp.cached_tools(server_row.id))
        chat = make_chat(db, ctx_size=8192, agent=True, agent_config={"mcp_server_ids": [server_row.id]})
        msg(db, chat, "user", "hi")

        inspected = ci.inspect_chat_context(db, chat)
        assert inspected["tool_count"] == 6  # the fixture server's six tools
        assert cat(inspected, "tool_definitions")["tokens"] > 0 and cat(inspected, "tool_definitions")["items"] == 6

        agent_loop.run_agent_turn(db, chat, llama.endpoint)
        sent = llama.requests[0]["tools"]
        assert len(sent) == inspected["tool_count"]
        assert ci.estimate_tokens(json.dumps(sent, ensure_ascii=False)) == cat(inspected, "tool_definitions")["tokens"]
    finally:
        llama.stop()
        mcp.shutdown()


def test_agent_without_connected_tools_has_no_tool_definition_cost(db):
    chat = make_chat(db, ctx_size=4096, agent=True, agent_config={"mcp_server_ids": [999]})
    msg(db, chat, "user", "hi")
    r = ci.inspect_chat_context(db, chat)
    assert r["tool_count"] == 0 and cat(r, "tool_definitions")["tokens"] == 0


# ---------------------------------------------------------------------
# API
# ---------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    from backend.core.metrics import metrics_recorder

    monkeypatch.setattr(metrics_recorder, "start", lambda: None)
    monkeypatch.setattr(metrics_recorder, "stop", lambda: None)
    from backend.main import app

    with TestClient(app) as c:
        yield c


def test_context_route_requires_token(client):
    assert client.get("/api/v1/chats/1/context").status_code == 401


def test_context_route_unknown_chat_is_404(client):
    assert client.get("/api/v1/chats/999999/context", headers=AUTH).status_code == 404


def test_context_route_offline_runtime_estimates(client, db):
    chat = make_chat(db, ctx_size=1000)
    msg(db, chat, "user", "x" * 400)
    r = client.get(f"/api/v1/chats/{chat.id}/context", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "estimate" and body["total_tokens"] == 100 and body["context_window"] == 1000


def test_context_route_online_runtime_uses_its_tokenizer(client, db, server, monkeypatch):
    from backend.core.runtime_manager import runtime_manager

    chat = make_chat(db, ctx_size=1000)
    msg(db, chat, "user", "one two three four")
    monkeypatch.setattr(
        runtime_manager, "get_status", lambda d, rt: EngineStatus(state=RuntimeState.ONLINE, endpoint=server.endpoint)
    )
    body = client.get(f"/api/v1/chats/{chat.id}/context", headers=AUTH).json()
    assert body["method"] == "tokenizer" and body["total_tokens"] == 4
