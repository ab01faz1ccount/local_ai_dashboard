"""
tests/backend/test_agent_loop_streaming.py

run_agent_turn's streaming mode (on_delta / on_message / cancel) against
the same real pieces as test_agent_loop.py -- a real MCP subprocess, a real
PermissionEngine, and a real HTTP server that now speaks real SSE.
"""

import json
import threading
import time

import pytest

from backend.core import agent_loop, chat as chat_engine
from backend.storage import db as storage_db
from backend.storage.db import Event, RequestLog

from fake_llama_server import FakeLlamaServer
# shared fixtures/helpers (same fixtures, not a copy: one definition to maintain)
from test_agent_loop import (  # noqa: F401
    _fresh_db, auto_allow, connect_fixture_server, db, event_types, llama, make_chat_with_agent, mcp, messages, perm, tool_call,
)


def turn(db, chat, endpoint, **kw):
    storage_db.add_chat_message(db, chat.id, role="user", content="go")
    return agent_loop.run_agent_turn(db, chat, endpoint, **kw)


def plain_chat(db):
    return make_chat_with_agent(db, mcp_server_ids=[])


def snapshot(db, chat_id):
    return [(m.role, m.content, (m.tool_meta_json or {}).get("status")) for m in messages(db, chat_id)]


# ---------------------------------------------------------------------
# basic streaming
# ---------------------------------------------------------------------

def test_deltas_arrive_in_order_and_equal_the_stored_reply(db, llama):
    chat = plain_chat(db)
    llama.set_responses([{"content": "the quick brown fox"}])
    deltas = []
    written = turn(db, chat, llama.endpoint, on_delta=deltas.append)
    assert deltas == ["the ", "quick ", "brown ", "fox"]
    assert len(written) == 1 and written[0].content == "".join(deltas)
    assert written[0].prompt_tokens == 10 and written[0].completion_tokens == 5  # usage came through the stream


def test_streaming_requests_are_stream_true_and_blocking_ones_are_not(db, llama):
    chat = plain_chat(db)
    turn(db, chat, llama.endpoint, on_delta=lambda t: None)
    assert llama.requests[0]["stream"] is True
    turn(db, chat, llama.endpoint)
    assert llama.requests[1]["stream"] is False and "stream_options" not in llama.requests[1]


def test_on_message_gets_each_stored_message_as_it_is_committed(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    llama.set_responses([
        {"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "hi"})]},
        {"content": "all done"},
    ])
    got = []
    stop = auto_allow(perm)
    try:
        written = turn(db, chat, llama.endpoint, on_delta=lambda t: None, on_message=lambda m: got.append((m.id, m.role)))
    finally:
        stop.set()
    assert [r for _, r in got] == ["assistant", "tool", "assistant"]
    assert [i for i, _ in got] == [m.id for m in written]
    assert all(i is not None for i, _ in got)  # already committed when announced


def test_streaming_and_blocking_produce_identical_transcripts(db, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    script = [
        {"content": "checking", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "a"}, "c1"), tool_call(f"{server_id}__add", {"a": 1, "b": 2}, "c2")]},
        {"content": "finished it"},
    ]
    results = {}
    for mode in ("blocking", "streaming"):
        chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
        srv = FakeLlamaServer(list(script))
        srv.start()
        stop = auto_allow(perm)
        try:
            kw = {"on_delta": lambda t: None} if mode == "streaming" else {}
            turn(db, chat, srv.endpoint, **kw)
        finally:
            stop.set()
            srv.stop()
        results[mode] = snapshot(db, chat.id)
    assert results["blocking"] == results["streaming"]
    assert [r for r, _, _ in results["streaming"]] == ["user", "assistant", "tool", "tool", "assistant"]


def test_tool_calls_survive_streaming_reassembly_into_the_stored_meta(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    llama.set_responses([
        {"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "hello streaming world"}, "abc")]},
        {"content": "ok"},
    ])
    stop = auto_allow(perm)
    try:
        written = turn(db, chat, llama.endpoint, on_delta=lambda t: None)
    finally:
        stop.set()
    assert written[0].tool_meta_json["calls"] == [{"id": "abc", "name": f"{server_id}__echo", "arguments": {"text": "hello streaming world"}}]
    assert written[1].content == "hello streaming world"


def test_max_steps_still_bounds_a_streaming_model_that_never_stops(db, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    srv = FakeLlamaServer([lambda _b: {"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "x"})]}])
    srv.start()
    stop = auto_allow(perm)
    try:
        written = turn(db, chat, srv.endpoint, on_delta=lambda t: None, max_steps=3)
    finally:
        stop.set()
        srv.stop()
    assert len([m for m in written if m.role == "tool" and m.tool_meta_json.get("status") == "ok"]) <= 3
    assert len(srv.requests) < 50


# ---------------------------------------------------------------------
# failures
# ---------------------------------------------------------------------

def test_a_stream_that_breaks_raises_and_stores_no_half_answer(db, llama):
    chat = plain_chat(db)
    llama.set_responses([{"content": "one two three four", "stream_break_after": 2}])
    with pytest.raises(chat_engine.ChatCompletionError):
        turn(db, chat, llama.endpoint, on_delta=lambda t: None)
    assert [m.role for m in messages(db, chat.id)] == ["user"]
    assert event_types("inference.") == ["inference.started", "inference.failed"]


def test_completion_failure_after_a_streamed_tool_step_keeps_the_tool_message(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    llama.set_responses([
        {"content": "", "tool_calls": [tool_call(f"{server_id}__echo", {"text": "x"})]},
        {"status": 500, "body": "boom"},
    ])
    stop = auto_allow(perm)
    try:
        with pytest.raises(chat_engine.ChatCompletionError):
            turn(db, chat, llama.endpoint, on_delta=lambda t: None)
    finally:
        stop.set()
    assert [m.role for m in messages(db, chat.id)] == ["user", "assistant", "tool"]


# ---------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------

def test_cancel_before_the_turn_starts_makes_no_request(db, llama):
    chat = plain_chat(db)
    cancel = threading.Event()
    cancel.set()
    written = turn(db, chat, llama.endpoint, on_delta=lambda t: None, cancel=cancel)
    assert written == [] and llama.requests == []


def test_cancel_mid_stream_keeps_the_text_that_was_already_shown(db, llama):
    chat = plain_chat(db)
    llama.set_responses([{"content": "w1 w2 w3 w4 w5 w6 w7 w8 w9", "stream_delay": 0.1}])
    cancel = threading.Event()
    seen = []

    def on_delta(t):
        seen.append(t)
        if len(seen) == 3:
            cancel.set()

    written = turn(db, chat, llama.endpoint, on_delta=on_delta, cancel=cancel)
    assert len(written) == 1 and written[0].role == "assistant"
    assert written[0].content.startswith("w1 w2 w3") and written[0].content != "w1 w2 w3 w4 w5 w6 w7 w8 w9"
    assert written[0].content == "".join(seen[: len(written[0].content.split())])
    assert written[0].prompt_tokens is None  # a cancelled reply has no usage to report
    with storage_db.SessionLocal() as s:
        assert s.query(RequestLog).filter(RequestLog.chat_id == chat.id).count() == 0  # not counted as a completed request
        failed = s.query(Event).filter(Event.event_type == "inference.failed").one()
    assert failed.metadata_json["cancelled"] is True


def test_cancel_between_tool_calls_leaves_a_valid_history(db, llama, mcp, perm):
    server_id = connect_fixture_server(db, mcp)
    chat = make_chat_with_agent(db, mcp_server_ids=[server_id])
    llama.set_responses([{"content": "", "tool_calls": [
        tool_call(f"{server_id}__echo", {"text": "first"}, "c1"),
        tool_call(f"{server_id}__echo", {"text": "second"}, "c2"),
    ]}])
    cancel = threading.Event()
    stop = auto_allow(perm)
    try:
        written = turn(db, chat, llama.endpoint, on_delta=lambda t: None, cancel=cancel,
                       on_message=lambda m: cancel.set() if m.role == "tool" else None)
    finally:
        stop.set()
    tools = [m for m in written if m.role == "tool"]
    assert [(t.tool_meta_json["call_id"], t.tool_meta_json["status"]) for t in tools] == [("c1", "ok"), ("c2", "error")]
    assert "cancelled" in tools[1].content
    # every tool call the assistant announced has a stored answer -- the next turn's history is well formed
    stored = messages(db, chat.id)
    announced = {c["id"] for m in stored if m.role == "assistant" for c in (m.tool_meta_json or {}).get("calls", [])}
    answered = {m.tool_meta_json["call_id"] for m in stored if m.role == "tool"}
    assert announced == answered
    assert len(llama.requests) == 1  # it did not go back to the model after cancelling


def test_a_conversation_continues_normally_after_a_cancelled_turn(db, llama):
    chat = plain_chat(db)
    llama.set_responses([{"content": "x1 x2 x3 x4 x5 x6", "stream_delay": 0.1}])
    cancel = threading.Event()
    n = []
    turn(db, chat, llama.endpoint, on_delta=lambda t: (n.append(t), cancel.set() if len(n) == 2 else None), cancel=cancel)
    llama.set_responses([{"content": "fresh answer"}])
    written = turn(db, chat, llama.endpoint, on_delta=lambda t: None)
    assert written[-1].content == "fresh answer"
    sent = llama.requests[-1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]  # the partial answer is in the history
