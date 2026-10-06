"""
tests/backend/test_chat_stream_api.py

POST /chats/{id}/messages/stream end to end through the real app: a real
SSE response over TestClient, a real fake-llama SSE upstream, the real agent
loop in a worker thread with its own DB session.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.core.engine.base import EngineStatus, RuntimeState
from backend.core.security import get_or_create_access_token
from backend.storage import db as storage_db
from backend.storage.db import Chat, Runtime

from fake_llama_server import FakeLlamaServer

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
def llama():
    s = FakeLlamaServer([{"content": "hello from the model"}])
    s.start()
    yield s
    s.stop()


@pytest.fixture
def client(monkeypatch, llama):
    from backend.core.metrics import metrics_recorder
    from backend.core.runtime_manager import runtime_manager

    monkeypatch.setattr(metrics_recorder, "start", lambda: None)
    monkeypatch.setattr(metrics_recorder, "stop", lambda: None)
    monkeypatch.setattr(
        runtime_manager, "get_status", lambda d, rt: EngineStatus(state=RuntimeState.ONLINE, endpoint=llama.endpoint)
    )
    from backend.main import app

    with TestClient(app) as c:
        yield c


def make_chat() -> int:
    with storage_db.SessionLocal() as s:
        rt = Runtime(name="r", executable_path="/bin/true", host="127.0.0.1", port=1)
        s.add(rt)
        s.commit()
        chat = Chat(runtime_id=rt.id)
        s.add(chat)
        s.commit()
        return chat.id


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for frame in text.strip().split("\n\n"):
        lines = frame.split("\n")
        event = next(l[7:] for l in lines if l.startswith("event: "))
        data = json.loads(next(l[6:] for l in lines if l.startswith("data: ")))
        out.append((event, data))
    return out


def send(client, chat_id, content="hi", **extra):
    with client.stream("POST", f"/api/v1/chats/{chat_id}/messages/stream", headers=AUTH, json={"content": content, **extra}) as r:
        body = "".join(r.iter_text())
        return r, body


def stored(chat_id):
    with storage_db.SessionLocal() as s:
        return [(m.role, m.content) for m in storage_db.get_chat_messages(s, chat_id)]


# ---------------------------------------------------------------------

def test_requires_the_token(client):
    r = client.post("/api/v1/chats/1/messages/stream", json={"content": "x"})
    assert r.status_code == 401


def test_response_is_an_event_stream_with_no_buffering_headers(client):
    r, _ = send(client, make_chat())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"


def test_event_order_user_then_deltas_then_message_then_done(client):
    chat_id = make_chat()
    _, body = send(client, chat_id, "say something")
    events = parse_sse(body)
    names = [e for e, _ in events]
    assert names[0] == "user" and names[-1] == "done"
    assert names.count("message") == 1 and names.index("message") > max(i for i, n in enumerate(names) if n == "delta")
    assert events[0][1]["content"] == "say something" and events[0][1]["role"] == "user"


def test_deltas_concatenate_to_the_stored_reply(client):
    chat_id = make_chat()
    _, body = send(client, chat_id)
    events = parse_sse(body)
    text = "".join(d["text"] for e, d in events if e == "delta")
    msg = next(d for e, d in events if e == "message")
    assert text == "hello from the model" == msg["content"]
    assert [d["text"] for e, d in events if e == "delta"] == ["hello ", "from ", "the ", "model"]


def test_done_lists_the_stored_message_ids_and_they_match_the_database(client):
    chat_id = make_chat()
    _, body = send(client, chat_id)
    events = parse_sse(body)
    done = events[-1][1]
    with storage_db.SessionLocal() as s:
        db_ids = [m.id for m in storage_db.get_chat_messages(s, chat_id) if m.role == "assistant"]
    assert done["message_ids"] == db_ids and len(db_ids) == 1


def test_everything_is_persisted_like_the_blocking_route(client):
    chat_id = make_chat()
    send(client, chat_id, "hi there")
    assert stored(chat_id) == [("user", "hi there"), ("assistant", "hello from the model")]


def test_the_message_event_carries_usage_and_tool_meta_fields(client):
    chat_id = make_chat()
    _, body = send(client, chat_id)
    msg = next(d for e, d in parse_sse(body) if e == "message")
    assert msg["prompt_tokens"] == 10 and msg["completion_tokens"] == 5 and msg["tool_meta"] == {}


def test_temperature_and_max_tokens_reach_the_model(client, llama):
    send(client, make_chat(), temperature=0.1, max_tokens=33)
    assert llama.requests[0]["temperature"] == 0.1 and llama.requests[0]["max_tokens"] == 33 and llama.requests[0]["stream"] is True


def test_unicode_survives_the_wire(client, llama):
    llama.set_responses([{"content": "سلام دنیا"}])
    chat_id = make_chat()
    _, body = send(client, chat_id, "سلام")
    events = parse_sse(body)
    assert "".join(d["text"] for e, d in events if e == "delta") == "سلام دنیا"
    assert events[0][1]["content"] == "سلام"


# ---------------------------------------------------------------------
# failures
# ---------------------------------------------------------------------

def test_unknown_chat_is_a_plain_404_before_any_stream(client):
    r = client.post("/api/v1/chats/999999/messages/stream", headers=AUTH, json={"content": "x"})
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_offline_runtime_is_a_plain_409_and_stores_nothing(client, monkeypatch):
    from backend.core.runtime_manager import runtime_manager

    monkeypatch.setattr(runtime_manager, "get_status", lambda d, rt: EngineStatus(state=RuntimeState.OFFLINE))
    chat_id = make_chat()
    r = client.post(f"/api/v1/chats/{chat_id}/messages/stream", headers=AUTH, json={"content": "x"})
    assert r.status_code == 409
    assert stored(chat_id) == []


def test_model_failure_is_an_error_event_then_done_and_the_user_message_is_kept(client, llama):
    llama.set_responses([{"status": 500, "body": "model exploded"}])
    chat_id = make_chat()
    _, body = send(client, chat_id, "hello?")
    events = parse_sse(body)
    names = [e for e, _ in events]
    assert names == ["user", "error", "done"]
    assert "500" in events[1][1]["detail"] and "model exploded" in events[1][1]["detail"]
    assert events[2][1]["message_ids"] == []
    assert stored(chat_id) == [("user", "hello?")]


def test_a_stream_that_dies_midway_reports_an_error_not_a_fake_success(client, llama):
    llama.set_responses([{"content": "a b c d e f", "stream_break_after": 2}])
    chat_id = make_chat()
    _, body = send(client, chat_id)
    names = [e for e, _ in parse_sse(body)]
    assert "error" in names and "message" not in names and names[-1] == "done"
    assert [r for r, _ in stored(chat_id)] == ["user"]


def test_an_internal_bug_still_ends_the_stream_cleanly(client, monkeypatch):
    from backend.core import agent_loop

    def boom(*a, **k):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(agent_loop, "run_agent_turn", boom)
    _, body = send(client, make_chat())
    events = parse_sse(body)
    assert [e for e, _ in events] == ["user", "error", "done"]
    assert "secret internals" not in body  # internals are not leaked to the client


def test_the_blocking_route_still_works_alongside(client):
    chat_id = make_chat()
    r = client.post(f"/api/v1/chats/{chat_id}/messages", headers=AUTH, json={"content": "hi"})
    assert r.status_code == 200 and r.json()["content"] == "hello from the model"


# ---------------------------------------------------------------------
# disconnect -> cancel
# ---------------------------------------------------------------------

# ---------------------------------------------------------------------
# a REAL server + REAL socket: TestClient buffers a streaming response, so
# it can neither show incremental delivery nor simulate a disconnect.
# ---------------------------------------------------------------------

import http.client
import socket
import threading

import uvicorn


@pytest.fixture
def live(monkeypatch, llama):
    from backend.core.metrics import metrics_recorder
    from backend.core.runtime_manager import runtime_manager

    monkeypatch.setattr(metrics_recorder, "start", lambda: None)
    monkeypatch.setattr(metrics_recorder, "stop", lambda: None)
    monkeypatch.setattr(
        runtime_manager, "get_status", lambda d, rt: EngineStatus(state=RuntimeState.ONLINE, endpoint=llama.endpoint)
    )
    from backend.main import app

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started
    yield sock.getsockname()[1]
    server.should_exit = True
    thread.join(timeout=10)


def open_stream(port, chat_id, content="go"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps({"content": content})
    conn.request("POST", f"/api/v1/chats/{chat_id}/messages/stream", body=body,
                 headers={**AUTH, "Content-Type": "application/json"})
    return conn, conn.getresponse()


def test_live_stream_is_delivered_incrementally_not_all_at_the_end(live, llama):
    llama.set_responses([{"content": " ".join(f"w{i}" for i in range(12)), "stream_delay": 0.1}])
    chat_id = make_chat()
    conn, resp = open_stream(live, chat_id)
    assert resp.status == 200 and resp.getheader("Content-Type").startswith("text/event-stream")
    t0 = time.time()
    first = b""
    while b"event: delta" not in first:
        first += resp.read1(4096)
    first_delta_at = time.time() - t0
    rest = resp.read()
    total = time.time() - t0
    conn.close()
    assert first_delta_at < total - 0.5, "the first delta only arrived once the whole reply was done"
    assert "event: done" in (first + rest).decode()


def test_live_disconnect_cancels_the_turn_and_keeps_the_partial_reply(live, llama):
    llama.set_responses([{"content": " ".join(f"w{i}" for i in range(60)), "stream_delay": 0.05}])
    chat_id = make_chat()
    conn, resp = open_stream(live, chat_id)
    got = b""
    while b"w3 " not in got:
        got += resp.read1(4096)
    conn.close()  # the browser's Stop button / closed tab

    deadline = time.time() + 8
    assistant = None
    while time.time() < deadline:
        rows = [c for role, c in stored(chat_id) if role == "assistant"]
        if rows:
            assistant = rows[0]
            break
        time.sleep(0.1)
    assert assistant is not None, "partial reply was never saved after the client disconnected"
    assert assistant.startswith("w0 w1 w2 w3") and len(assistant.split()) < 60
    time.sleep(0.5)
    assert len([1 for role, _ in stored(chat_id) if role == "assistant"]) == 1  # exactly one, not duplicated
