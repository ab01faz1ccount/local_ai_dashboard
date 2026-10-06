"""
tests/backend/test_session_export.py

core/session_export.py: session_summary/session_events/the three export
formats, against a real llm_agent_sessions row + real Event rows (via
events.emit, not hand-built dicts, so the actual metadata shape is
exercised).
"""

import json

import pytest
from sqlalchemy.orm import sessionmaker

from backend.core import events
from backend.core import session_export
from backend.storage import db as storage_db
from backend.storage.db import Agent, LlmAgentSession, RequestLog, Runtime


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
def link(db) -> LlmAgentSession:
    runtime = Runtime(name="my-runtime", executable_path="/bin/true", host="127.0.0.1", port=1)
    agent = Agent(name="my-agent", agent_type="generic")
    db.add_all([runtime, agent])
    db.commit()
    link = storage_db.start_llm_agent_session(db, runtime.id, agent.id)
    return link


def test_session_summary_has_names_and_stats(db, link):
    s = session_export.session_summary(db, link)
    assert s["id"] == link.id
    assert s["runtime_name"] == "my-runtime" and s["agent_name"] == "my-agent"
    assert s["status"] == "ACTIVE" and s["requests_count"] == 0
    assert s["chat_ids"] == []


def test_session_summary_with_a_deleted_runtime_or_agent_has_none_names(db, link):
    # session_link_id columns are ON DELETE SET NULL on request_logs but
    # llm_agent_sessions.runtime_id/agent_id are ON DELETE CASCADE -- so
    # exercise the "row still exists, FK target doesn't" case directly.
    s = session_export.session_summary(db, LlmAgentSession(id=999, runtime_id=777, agent_id=888, started_at="x"))
    assert s["runtime_name"] is None and s["agent_name"] is None


def test_chat_ids_for_session_are_distinct_and_sorted(db, link):
    chat_a = storage_db.create_chat(db, link.runtime_id, link.agent_id)
    chat_b = storage_db.create_chat(db, link.runtime_id, link.agent_id)
    storage_db.record_request(db, runtime_id=link.runtime_id, agent_id=link.agent_id, prompt_tokens=1, completion_tokens=1, latency_ms=1, session_link_id=link.id, chat_id=chat_b.id)
    storage_db.record_request(db, runtime_id=link.runtime_id, agent_id=link.agent_id, prompt_tokens=1, completion_tokens=1, latency_ms=1, session_link_id=link.id, chat_id=chat_a.id)
    storage_db.record_request(db, runtime_id=link.runtime_id, agent_id=link.agent_id, prompt_tokens=1, completion_tokens=1, latency_ms=1, session_link_id=link.id, chat_id=chat_b.id)
    assert session_export.chat_ids_for_session(db, link.id) == sorted([chat_a.id, chat_b.id])


def test_requests_with_no_chat_id_are_excluded(db, link):
    storage_db.record_request(db, runtime_id=link.runtime_id, agent_id=link.agent_id, prompt_tokens=1, completion_tokens=1, latency_ms=1, session_link_id=link.id, chat_id=None)
    assert session_export.chat_ids_for_session(db, link.id) == []


def test_session_events_are_chronological_and_scoped_to_the_session(db, link):
    events.emit(events.EventType.INFERENCE_STARTED, session_id=link.id, metadata={"n": 1})
    events.emit(events.EventType.INFERENCE_COMPLETED, session_id=link.id, metadata={"n": 2})
    events.emit(events.EventType.INFERENCE_STARTED, session_id=None, metadata={"n": "unrelated"})  # different session

    evs = session_export.session_events(db, link.id)
    assert [e["metadata"]["n"] for e in evs] == [1, 2]
    assert [e["event_type"] for e in evs] == ["inference.started", "inference.completed"]


def test_no_events_gives_an_empty_list(db, link):
    assert session_export.session_events(db, link.id) == []


# ---------------------------------------------------------------------
# JSON / JSONL export
# ---------------------------------------------------------------------

def test_export_json_round_trips(db, link):
    events.emit(events.EventType.TOOL_CALLED, session_id=link.id, metadata={"tool": "echo"})
    doc = json.loads(session_export.export_json(db, link))
    assert doc["session"]["id"] == link.id
    assert doc["events"][0]["event_type"] == "tool.called"
    assert doc["events"][0]["metadata"]["tool"] == "echo"


def test_export_jsonl_is_one_json_object_per_line(db, link):
    events.emit(events.EventType.TOOL_CALLED, session_id=link.id, metadata={"tool": "echo"})
    events.emit(events.EventType.TOOL_COMPLETED, session_id=link.id, metadata={"tool": "echo"})
    lines = session_export.export_jsonl(db, link).strip("\n").split("\n")
    assert len(lines) == 3  # 1 session header + 2 events
    parsed = [json.loads(l) for l in lines]
    assert parsed[0]["type"] == "session" and parsed[0]["id"] == link.id
    assert parsed[1]["type"] == "event" and parsed[1]["event_type"] == "tool.called"
    assert parsed[2]["event_type"] == "tool.completed"


def test_export_jsonl_with_no_events_is_just_the_header(db, link):
    lines = session_export.export_jsonl(db, link).strip("\n").split("\n")
    assert len(lines) == 1


# ---------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------

def test_export_markdown_has_summary_and_narrative_lines(db, link):
    events.emit(events.EventType.INFERENCE_COMPLETED, session_id=link.id, metadata={"prompt_tokens": 10, "completion_tokens": 5, "latency_ms": 123})
    events.emit(events.EventType.TOOL_CALLED, session_id=link.id, metadata={"tool": "echo", "arguments": {"text": "hi"}})
    events.emit(events.EventType.TOOL_FAILED, session_id=link.id, metadata={"tool": "echo", "reason": "permission_denied"})
    events.emit(events.EventType.PERMISSION_DENIED, session_id=link.id, metadata={"reason": "timeout"})

    md = session_export.export_markdown(db, link)
    assert "# Session #" in md
    assert "my-runtime" in md and "my-agent" in md
    assert "10 prompt / 5 completion tokens" in md
    assert "123 ms" in md
    assert "Called tool `echo` with `{\"text\": \"hi\"}`" in md
    assert "permission_denied" in md
    assert "Permission denied. (timeout)" in md


def test_export_markdown_with_no_events_says_so(db, link):
    md = session_export.export_markdown(db, link)
    assert "_No events recorded._" in md


def test_export_markdown_unknown_event_type_falls_back_to_the_raw_name(db, link):
    events.emit("some.made.up.type", session_id=link.id, metadata={})
    md = session_export.export_markdown(db, link)
    assert "`some.made.up.type`" in md


def test_export_formats_registry_has_all_three():
    assert set(session_export.EXPORT_FORMATS) == {"json", "jsonl", "markdown"}
    for builder, media_type, ext in session_export.EXPORT_FORMATS.values():
        assert callable(builder) and media_type and ext
