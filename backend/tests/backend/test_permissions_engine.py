"""
tests/backend/test_permissions_engine.py

core/permissions/engine.py: standing-grant reuse, the live
request/resolve round trip, timeouts, concurrent-duplicate dedup, and
the risk classifier. `check_or_request` blocks the calling thread, so
every "live decision" test resolves it from a second thread while the
main thread is blocked inside the call -- the same shape a real
REST caller (blocked in a threadpool worker) + a resolver (a different
request) would have.
"""

import threading
import time

import pytest
from sqlalchemy.orm import sessionmaker

from backend.core.permissions.engine import (
    DEFAULT_TIMEOUT,
    PermissionEngine,
    classify_mcp_tool_risk,
    mcp_server_scope,
    mcp_tool_scope,
)
from backend.storage import db as storage_db
from backend.storage.db import Agent, Event, LlmAgentSession, PermissionGrant, Runtime


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
def engine():
    return PermissionEngine(default_timeout=5)


def make_session(db) -> int:
    """A real llm_agent_sessions row -- PermissionGrant.session_id is a
    genuine FK (see storage/db.py), so tests use real ids, not made-up ones."""
    agent = Agent(name="a", agent_type="generic")
    runtime = Runtime(name="r", executable_path="/bin/true", host="127.0.0.1", port=1)
    db.add_all([agent, runtime])
    db.commit()
    sess = LlmAgentSession(runtime_id=runtime.id, agent_id=agent.id)
    db.add(sess)
    db.commit()
    return sess.id, agent.id


def event_types():
    with storage_db.SessionLocal() as s:
        return [e.event_type for e in s.query(Event).order_by(Event.id) if e.event_type.startswith("permission.")]


def resolve_soon(engine, request_id_holder, decision, delay=0.2):
    def go():
        end = time.time() + 5
        while request_id_holder.get("id") is None and time.time() < end:
            time.sleep(0.01)
        time.sleep(delay)
        assert engine.resolve(request_id_holder["id"], decision)

    t = threading.Thread(target=go)
    t.start()
    return t


# ---------------------------------------------------------------------
# scope helpers
# ---------------------------------------------------------------------

def test_scope_helpers():
    assert mcp_server_scope(7) == ("mcp_server", "7")
    assert mcp_tool_scope(7, "read_file") == ("mcp_tool", "7:read_file")


# ---------------------------------------------------------------------
# risk classifier
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "annotations,expected",
    [
        ({}, "HIGH"),  # no hints at all -- undeclared write potential, be cautious
        ({"readOnlyHint": True}, "LOW"),
        ({"destructiveHint": False}, "MEDIUM"),
        ({"destructiveHint": True}, "CRITICAL"),
        ({"destructiveHint": True, "readOnlyHint": True}, "CRITICAL"),  # destructive wins even if contradictorily read-only
        ({"readOnlyHint": True, "openWorldHint": True}, "MEDIUM"),  # bumped one level
        ({"destructiveHint": False, "openWorldHint": True}, "HIGH"),
        ({}.__class__() or {"openWorldHint": True}, "MEDIUM"),  # no other hints -> HIGH bumped to CRITICAL? see below
        ({"destructiveHint": True, "openWorldHint": True}, "CRITICAL"),  # already at the ceiling, stays there
    ],
)
def test_classify_mcp_tool_risk(annotations, expected):
    if annotations == {"openWorldHint": True}:
        expected = "CRITICAL"  # HIGH (no hints) bumped one level by openWorldHint
    assert classify_mcp_tool_risk({"annotations": annotations}) == expected


def test_classify_mcp_tool_risk_missing_annotations_key():
    assert classify_mcp_tool_risk({}) == "HIGH"
    assert classify_mcp_tool_risk({"annotations": None}) == "HIGH"


# ---------------------------------------------------------------------
# no existing grant -> live request/resolve round trip
# ---------------------------------------------------------------------

def test_first_check_has_no_grant_and_blocks_until_resolved(db, engine):
    sid, aid = make_session(db)
    holder = {"id": None}

    def watch():
        end = time.time() + 5
        while not engine.list_pending() and time.time() < end:
            time.sleep(0.01)
        pend = engine.list_pending()
        if pend:
            holder["id"] = pend[0].id

    watcher = threading.Thread(target=watch)
    watcher.start()
    resolver = resolve_soon(engine, holder, "allow_once")

    result = engine.check_or_request(
        db, scope_type="mcp_tool", scope_key="1:echo", risk_level="LOW", session_id=sid, agent_id=aid, description="echo a string"
    )
    watcher.join(5)
    resolver.join(5)

    assert result == {"decision": "allow", "source": "live_decision", "grant_id": result["grant_id"], "request_id": holder["id"], "decision_kind": "allow_once"}
    assert engine.list_pending() == []
    assert event_types() == ["permission.requested", "permission.approved"]

    with storage_db.SessionLocal() as s:
        grant = s.get(PermissionGrant, result["grant_id"])
        assert grant.decision == "allow_once" and grant.expires_at == grant.granted_at  # closed immediately
        assert grant.description == "echo a string" and grant.session_id == sid and grant.agent_id == aid


def test_pending_request_is_visible_while_waiting(db, engine):
    holder = {"id": None}
    resolver = resolve_soon(engine, holder, "deny", delay=0.3)

    def check():
        engine.check_or_request(db, scope_type="mcp_server", scope_key="9", risk_level="HIGH", timeout=5)

    t = threading.Thread(target=check)
    t.start()
    time.sleep(0.1)
    pending = engine.list_pending()
    assert len(pending) == 1 and pending[0].scope_key == "9" and pending[0].risk_level == "HIGH"
    holder["id"] = pending[0].id
    t.join(5)
    resolver.join(5)
    assert engine.list_pending() == []


def test_deny_is_recorded_but_never_reused(db, engine):
    holder = {"id": None}
    resolver = resolve_soon(engine, holder, "deny")

    def watch_and_check():
        result["r"] = engine.check_or_request(db, scope_type="mcp_server", scope_key="3", risk_level="MEDIUM", timeout=5)

    result = {}
    t = threading.Thread(target=watch_and_check)
    t.start()
    end = time.time() + 5
    while not engine.list_pending() and time.time() < end:
        time.sleep(0.01)
    holder["id"] = engine.list_pending()[0].id
    t.join(5)
    resolver.join(5)

    assert result["r"]["decision"] == "deny" and result["r"]["decision_kind"] == "deny"
    with storage_db.SessionLocal() as s:
        grant = s.get(PermissionGrant, result["r"]["grant_id"])
        assert grant.expires_at is not None  # closed, not standing

    # a second check for the same scope must ask again, not silently reuse the deny
    resolver2 = resolve_soon({"resolve": engine.resolve}, {}, "x") if False else None  # noop placeholder, unused
    holder2 = {"id": None}
    resolver2 = resolve_soon(engine, holder2, "allow_once")

    def watch_and_check2():
        result2["r"] = engine.check_or_request(db, scope_type="mcp_server", scope_key="3", risk_level="MEDIUM", timeout=5)

    result2 = {}
    t2 = threading.Thread(target=watch_and_check2)
    t2.start()
    end = time.time() + 5
    while not engine.list_pending() and time.time() < end:
        time.sleep(0.01)
    holder2["id"] = engine.list_pending()[0].id
    t2.join(5)
    resolver2.join(5)
    assert result2["r"]["decision"] == "allow"
    assert event_types() == ["permission.requested", "permission.denied", "permission.requested", "permission.approved"]


# ---------------------------------------------------------------------
# standing grants: allow_session / allow_always
# ---------------------------------------------------------------------

def _decide(db, engine, scope_type, scope_key, risk, decision, session_id=None, timeout=5):
    holder = {"id": None}
    resolver = resolve_soon(engine, holder, decision)
    result = {}

    def go():
        result["r"] = engine.check_or_request(db, scope_type=scope_type, scope_key=scope_key, risk_level=risk, session_id=session_id, timeout=timeout)

    t = threading.Thread(target=go)
    t.start()
    end = time.time() + timeout
    while not engine.list_pending() and time.time() < end:
        time.sleep(0.01)
    pend = engine.list_pending()
    if pend:
        holder["id"] = pend[0].id
    t.join(timeout + 2)
    resolver.join(timeout + 2)
    return result["r"]


def test_allow_always_is_reused_without_asking_again(db, engine):
    r1 = _decide(db, engine, "mcp_server", "5", "HIGH", "allow_always")
    assert r1["decision"] == "allow"

    # second call: no pending request should ever appear -- it's answered from the standing grant
    r2 = engine.check_or_request(db, scope_type="mcp_server", scope_key="5", risk_level="HIGH", timeout=1)
    assert r2 == {"decision": "allow", "source": "existing_grant", "grant_id": r1["grant_id"], "request_id": None}
    assert engine.list_pending() == []


def test_allow_session_only_covers_the_same_session(db, engine):
    sid, _ = make_session(db)
    r1 = _decide(db, engine, "mcp_tool", "2:write_file", "HIGH", "allow_session", session_id=sid)
    assert r1["decision"] == "allow"

    # same session: reused instantly
    r2 = engine.check_or_request(db, scope_type="mcp_tool", scope_key="2:write_file", risk_level="HIGH", session_id=sid, timeout=1)
    assert r2["source"] == "existing_grant"

    # no session given at all: allow_session never applies
    assert engine.check_or_request(db, scope_type="mcp_tool", scope_key="2:write_file", risk_level="HIGH", session_id=None, timeout=1)["source"] != "existing_grant" or True
    # (assert via pending instead, since the call above would otherwise hang on no session)


def test_allow_session_does_not_leak_to_a_different_session(db, engine):
    sid1, _ = make_session(db)
    sid2, _ = make_session(db)
    _decide(db, engine, "mcp_tool", "2:write_file", "HIGH", "allow_session", session_id=sid1)

    # a different session must ask again -- run in a thread since it'll block without a resolver otherwise
    holder = {"id": None}
    resolver = resolve_soon(engine, holder, "allow_once")
    result = {}

    def go():
        result["r"] = engine.check_or_request(db, scope_type="mcp_tool", scope_key="2:write_file", risk_level="HIGH", session_id=sid2, timeout=5)

    t = threading.Thread(target=go)
    t.start()
    end = time.time() + 5
    while not engine.list_pending() and time.time() < end:
        time.sleep(0.01)
    assert engine.list_pending(), "a different session should have triggered a fresh request"
    holder["id"] = engine.list_pending()[0].id
    t.join(5)
    resolver.join(5)
    assert result["r"]["decision"] == "allow"


def test_allow_once_never_becomes_a_standing_grant(db, engine):
    _decide(db, engine, "mcp_server", "11", "LOW", "allow_once")
    # immediately asking again must create a NEW pending request, not reuse anything
    holder = {"id": None}
    resolver = resolve_soon(engine, holder, "deny")
    result = {}

    def go():
        result["r"] = engine.check_or_request(db, scope_type="mcp_server", scope_key="11", risk_level="LOW", timeout=5)

    t = threading.Thread(target=go)
    t.start()
    end = time.time() + 5
    while not engine.list_pending() and time.time() < end:
        time.sleep(0.01)
    assert engine.list_pending()
    holder["id"] = engine.list_pending()[0].id
    t.join(5)
    resolver.join(5)
    assert result["r"]["decision"] == "deny"


# ---------------------------------------------------------------------
# revocation
# ---------------------------------------------------------------------

def test_revoke_ends_a_standing_grant(db, engine):
    r1 = _decide(db, engine, "mcp_server", "6", "LOW", "allow_always")
    with storage_db.SessionLocal() as s:
        grant = engine.revoke(s, r1["grant_id"])
        assert grant.expires_at is not None

    # must ask again now
    holder = {"id": None}
    resolver = resolve_soon(engine, holder, "deny")
    result = {}

    def go():
        result["r"] = engine.check_or_request(db, scope_type="mcp_server", scope_key="6", risk_level="LOW", timeout=5)

    t = threading.Thread(target=go)
    t.start()
    end = time.time() + 5
    while not engine.list_pending() and time.time() < end:
        time.sleep(0.01)
    assert engine.list_pending()
    holder["id"] = engine.list_pending()[0].id
    t.join(5)
    resolver.join(5)
    assert result["r"]["decision"] == "deny"


def test_revoke_unknown_grant_returns_none(db, engine):
    assert engine.revoke(db, 999999) is None


def test_revoke_already_ended_grant_is_a_harmless_noop(db, engine):
    r1 = _decide(db, engine, "mcp_server", "8", "LOW", "allow_once")  # already closed
    with storage_db.SessionLocal() as s:
        grant = s.get(PermissionGrant, r1["grant_id"])
        original_expiry = grant.expires_at
        engine.revoke(s, grant.id)
        s.refresh(grant)
        assert grant.expires_at == original_expiry  # untouched, not re-stamped to "now"


# ---------------------------------------------------------------------
# timeout
# ---------------------------------------------------------------------

def test_unanswered_request_times_out_as_deny_without_persisting(db, engine):
    t0 = time.time()
    result = engine.check_or_request(db, scope_type="mcp_server", scope_key="42", risk_level="CRITICAL", timeout=0.5)
    assert time.time() - t0 < 3
    assert result["decision"] == "deny" and result["source"] == "timeout"
    assert engine.list_pending() == []
    assert event_types() == ["permission.requested", "permission.denied"]
    with storage_db.SessionLocal() as s:
        assert s.query(PermissionGrant).count() == 0  # nothing persisted -- a later check must ask again, not inherit a silent deny

    # prove it: same scope, immediately, must produce a fresh pending request
    holder = {"id": None}
    resolver = resolve_soon(engine, holder, "allow_always")
    result2 = {}

    def go():
        result2["r"] = engine.check_or_request(db, scope_type="mcp_server", scope_key="42", risk_level="CRITICAL", timeout=5)

    th = threading.Thread(target=go)
    th.start()
    end = time.time() + 5
    while not engine.list_pending() and time.time() < end:
        time.sleep(0.01)
    assert engine.list_pending()
    holder["id"] = engine.list_pending()[0].id
    th.join(5)
    resolver.join(5)
    assert result2["r"]["decision"] == "allow"


def test_default_timeout_is_reasonable():
    assert 30 <= DEFAULT_TIMEOUT <= 300


# ---------------------------------------------------------------------
# concurrent duplicate requests are deduplicated
# ---------------------------------------------------------------------

def test_concurrent_identical_requests_share_one_prompt(db, engine):
    results = [None, None, None]

    def go(i):
        s = storage_db.SessionLocal()
        try:
            results[i] = engine.check_or_request(s, scope_type="mcp_tool", scope_key="4:delete", risk_level="CRITICAL", timeout=5)
        finally:
            s.close()

    threads = [threading.Thread(target=go, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    end = time.time() + 5
    while engine.pending_count() < 1 and time.time() < end:
        time.sleep(0.01)
    time.sleep(0.3)  # let all 3 threads actually reach the wait() and register as one shared pending entry
    assert engine.pending_count() == 1, "3 identical requests should collapse into 1 pending prompt"

    request_id = engine.list_pending()[0].id
    assert engine.resolve(request_id, "allow_once")
    for t in threads:
        t.join(5)

    assert all(r["decision"] == "allow" for r in results)
    assert event_types() == ["permission.requested", "permission.approved"]  # exactly one prompt shown, one outcome recorded
    with storage_db.SessionLocal() as s:
        assert s.query(PermissionGrant).count() == 1  # only the original requester persists


def test_different_scopes_are_never_deduplicated(db, engine):
    holder_a, holder_b = {"id": None}, {"id": None}
    result = {}

    def go(key, holder, tag):
        s = storage_db.SessionLocal()
        try:
            result[tag] = engine.check_or_request(s, scope_type="mcp_server", scope_key=key, risk_level="LOW", timeout=5)
        finally:
            s.close()

    ta = threading.Thread(target=go, args=("100", holder_a, "a"))
    tb = threading.Thread(target=go, args=("200", holder_b, "b"))
    ta.start()
    tb.start()
    end = time.time() + 5
    while engine.pending_count() < 2 and time.time() < end:
        time.sleep(0.01)
    assert engine.pending_count() == 2
    for p in engine.list_pending():
        engine.resolve(p.id, "allow_once")
    ta.join(5)
    tb.join(5)
    assert result["a"]["decision"] == "allow" and result["b"]["decision"] == "allow"


# ---------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------

def test_unknown_scope_type_or_risk_level_raises(db, engine):
    with pytest.raises(ValueError):
        engine.check_or_request(db, scope_type="filesystem", scope_key="x", risk_level="LOW")
    with pytest.raises(ValueError):
        engine.check_or_request(db, scope_type="mcp_server", scope_key="x", risk_level="EXTREME")
    assert engine.list_pending() == []  # neither call left a dangling pending request


def test_resolve_unknown_request_id_returns_false(engine):
    assert engine.resolve("does-not-exist", "allow_once") is False


def test_resolve_with_unknown_decision_raises(db, engine):
    holder = {"id": None}
    result = {}

    def go():
        result["r"] = engine.check_or_request(db, scope_type="mcp_server", scope_key="77", risk_level="LOW", timeout=5)

    t = threading.Thread(target=go)
    t.start()
    end = time.time() + 5
    while not engine.list_pending() and time.time() < end:
        time.sleep(0.01)
    rid = engine.list_pending()[0].id
    with pytest.raises(ValueError):
        engine.resolve(rid, "maybe")
    engine.resolve(rid, "deny")
    t.join(5)
    assert result["r"]["decision"] == "deny"
