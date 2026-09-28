"""
tests/backend/test_ws_events.py

/ws/events (api/ws.py): token auth, and that an event emitted after the
socket connects arrives as JSON on the wire. Uses the process-wide
event_bus singleton directly (the same one core/events/bus.py's
module-level `emit()` publishes through) rather than going through a
full HTTP route, since the point here is the WebSocket <-> EventBus
plumbing, not any particular emission call site (those are covered in
test_runtime_manager_events.py / test_events_api.py).
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import ws
from backend.core import events
from backend.core.security import get_or_create_access_token


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from backend.storage import db as storage_db

    fresh_engine = storage_db.make_engine(str(tmp_path / "t.sqlite3"))
    storage_db.Base.metadata.create_all(fresh_engine)
    monkeypatch.setattr(storage_db, "engine", fresh_engine)
    monkeypatch.setattr(
        storage_db, "SessionLocal", sessionmaker(bind=fresh_engine, autoflush=False, expire_on_commit=False, future=True)
    )
    yield


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(ws.router)
    return TestClient(app)


TOKEN = get_or_create_access_token()


def test_rejects_without_a_valid_token(client):
    with pytest.raises(Exception):  # starlette raises on the failed handshake/immediate close
        with client.websocket_connect("/ws/events?token=wrong"):
            pass


def test_subscriber_count_returns_to_zero_after_disconnect(client):
    assert events.event_bus.subscriber_count() == 0
    with client.websocket_connect(f"/ws/events?token={TOKEN}"):
        assert events.event_bus.subscriber_count() == 1
    assert events.event_bus.subscriber_count() == 0


def test_emitted_event_arrives_as_json_over_the_socket(client):
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as socket:
        events.emit(events.EventType.RUNTIME_STARTED, metadata={"note": "hello"})
        raw = socket.receive_text()
    payload = json.loads(raw)
    assert payload["event_type"] == "runtime.started"
    assert payload["metadata"] == {"note": "hello"}
    assert payload["event_id"]  # non-empty


def test_events_arrive_in_emission_order(client):
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as socket:
        events.emit(events.EventType.AGENT_CREATED, metadata={"n": 1})
        events.emit(events.EventType.AGENT_DELETED, metadata={"n": 2})
        first = json.loads(socket.receive_text())
        second = json.loads(socket.receive_text())
    assert [first["event_type"], second["event_type"]] == ["agent.created", "agent.deleted"]


def test_two_concurrent_sockets_each_get_the_event(client):
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as s1, client.websocket_connect(
        f"/ws/events?token={TOKEN}"
    ) as s2:
        events.emit(events.EventType.SESSION_STARTED, metadata={"n": 1})
        p1 = json.loads(s1.receive_text())
        p2 = json.loads(s2.receive_text())
    assert p1["event_type"] == p2["event_type"] == "session.started"
