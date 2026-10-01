"""
tests/backend/test_mcp_manager.py

core/mcp/manager.py against a REAL stdio MCP server
(mcp_fixture_server.py, built on the official SDK) -- so the handshake,
tool listing, child-process lifecycle, crash detection and timeouts are
the real thing, not a mock of them. Only transport *wiring* for http/sse
and secret redaction use a patched transport, since those need a
network peer / a controlled failure message.
"""

import asyncio
import contextlib
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest
from sqlalchemy.orm import sessionmaker

from backend.core.mcp.manager import (
    CONNECTED, CONNECTING, DISCONNECTED, ERROR, McpBusyError, McpManager, ServerConfig, _flatten_exception, _redact,
)
from backend.storage import db as storage_db
from backend.storage.db import Event, McpServer

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
    session = storage_db.SessionLocal()
    yield session
    session.close()


@pytest.fixture
def mgr():
    m = McpManager(connect_timeout=20, ping_interval=0.4, ping_timeout=3)
    yield m
    m.shutdown()


def make_server(db, name="fx", extra_args=(), env=None, **fields) -> McpServer:
    row = McpServer(
        name=name,
        transport=fields.pop("transport", "stdio"),
        command=fields.pop("command", sys.executable),
        args_json={"args": [FIXTURE, *extra_args]},
        env_json=env or {},
        **fields,
    )
    db.add(row)
    db.commit()
    return row


def event_types(types_prefix="mcp.") -> list[str]:
    with storage_db.SessionLocal() as s:
        return [e.event_type for e in s.query(Event).order_by(Event.id) if e.event_type.startswith(types_prefix)]


def fixture_processes() -> list[psutil.Process]:
    out = []
    for p in psutil.Process().children(recursive=True):
        with contextlib.suppress(psutil.Error):
            if any("mcp_fixture_server" in part for part in p.cmdline()):
                out.append(p)
    return out


def wait_until(predicate, timeout=15.0, interval=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ---------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------

def test_connect_real_stdio_server_lists_its_tools(db, mgr):
    row = make_server(db)
    status = mgr.connect(db, row)
    assert status.state == CONNECTED and status.error is None
    assert status.tools_count == 6
    assert status.server_info["name"] == "fixture-server"

    assert row.status == CONNECTED and row.tools_count == 6 and row.last_error is None
    assert row.server_info_json["name"] == "fixture-server"
    assert mgr.is_connected(row.id)

    tools = {t["name"]: t for t in mgr.list_tools(row.id)}
    assert set(tools) == {"echo", "add", "env_value", "delete_file", "fail", "slow"}
    assert tools["add"]["description"] == "Add two integers."
    assert set(tools["add"]["input_schema"]["properties"]) == {"a", "b"}
    assert event_types() == ["mcp.connected"]


def test_connected_event_carries_identifying_metadata(db, mgr):
    row = make_server(db)
    mgr.connect(db, row)
    with storage_db.SessionLocal() as s:
        ev = s.query(Event).filter(Event.event_type == "mcp.connected").one()
    assert ev.metadata_json["server_id"] == row.id
    assert ev.metadata_json["name"] == "fx"
    assert ev.metadata_json["transport"] == "stdio"
    assert ev.metadata_json["tools_count"] == 6


def test_connect_is_idempotent_and_emits_once(db, mgr):
    row = make_server(db)
    mgr.connect(db, row)
    again = mgr.connect(db, row)
    assert again.state == CONNECTED and again.tools_count == 6
    assert event_types() == ["mcp.connected"]
    assert len(fixture_processes()) == 1  # not a second child


def test_pagination_collects_every_tool(db, mgr):
    row = make_server(db, extra_args=["--many-tools"])
    assert mgr.connect(db, row).tools_count == 156
    assert len(mgr.list_tools(row.id)) == 156


def test_two_servers_are_independent(db, mgr):
    a, b = make_server(db, "a"), make_server(db, "b")
    mgr.connect(db, a)
    mgr.connect(db, b)
    assert mgr.is_connected(a.id) and mgr.is_connected(b.id)
    assert len(fixture_processes()) == 2
    mgr.disconnect(db, a)
    assert not mgr.is_connected(a.id) and mgr.is_connected(b.id)
    assert len(fixture_processes()) == 1


def test_reconnect_after_disconnect_gets_a_fresh_session(db, mgr):
    row = make_server(db)
    mgr.connect(db, row)
    mgr.disconnect(db, row)
    assert mgr.connect(db, row).state == CONNECTED
    assert row.status == CONNECTED
    assert event_types() == ["mcp.connected", "mcp.disconnected", "mcp.connected"]


# ---------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------

def test_disconnect_stops_the_child_process_and_updates_the_row(db, mgr):
    row = make_server(db)
    mgr.connect(db, row)
    assert len(fixture_processes()) == 1

    status = mgr.disconnect(db, row)
    assert status.state == DISCONNECTED
    assert row.status == DISCONNECTED and row.tools_count == 0
    assert not mgr.is_connected(row.id)
    assert wait_until(lambda: not fixture_processes(), timeout=10), "child process outlived disconnect"
    assert event_types() == ["mcp.connected", "mcp.disconnected"]
    with storage_db.SessionLocal() as s:
        ev = s.query(Event).filter(Event.event_type == "mcp.disconnected").one()
    assert ev.metadata_json["reason"] == "requested"


def test_requested_disconnect_is_never_reported_as_lost(db, mgr):
    row = make_server(db)
    mgr.connect(db, row)
    mgr.disconnect(db, row)
    time.sleep(1.2)  # > ping interval: a wrongly-armed loss handler would fire by now
    assert event_types().count("mcp.disconnected") == 1
    assert row.status == DISCONNECTED


def test_a_link_failure_racing_a_requested_stop_is_not_reported_as_lost(db, mgr, monkeypatch):
    # Simulates the ping failing at the same moment the user disconnects:
    # the supervisor reports an error AFTER a stop was requested. That must
    # not turn into a second, misleading mcp.disconnected(reason=lost).
    async def racing_supervise(live, session):
        await live.stop_event.wait()
        return "connection lost (raced with stop)"

    monkeypatch.setattr(mgr, "_supervise", racing_supervise)
    row = make_server(db)
    mgr.connect(db, row)
    mgr.disconnect(db, row)
    time.sleep(0.5)
    assert event_types() == ["mcp.connected", "mcp.disconnected"]
    with storage_db.SessionLocal() as s:
        ev = s.query(Event).filter(Event.event_type == "mcp.disconnected").one()
    assert ev.metadata_json["reason"] == "requested"
    assert row.status == DISCONNECTED


def test_a_link_failure_with_no_stop_requested_is_reported_as_lost(db, mgr, monkeypatch):
    # The counterpart: same failure, but nobody asked to stop -> it IS a loss.
    async def failing_supervise(live, session):
        await asyncio.sleep(0.2)
        return "connection lost (test)"

    monkeypatch.setattr(mgr, "_supervise", failing_supervise)
    row = make_server(db)
    mgr.connect(db, row)
    assert wait_until(lambda: "mcp.disconnected" in event_types(), timeout=10)
    with storage_db.SessionLocal() as s:
        ev = s.query(Event).filter(Event.event_type == "mcp.disconnected").one()
        assert ev.metadata_json["reason"] == "lost"
        assert s.get(McpServer, row.id).status == ERROR


def test_disconnect_when_never_connected_is_a_quiet_noop(db, mgr):
    row = make_server(db)
    assert mgr.disconnect(db, row).state == DISCONNECTED
    assert event_types() == []


def test_disconnect_clears_a_stale_connected_row_and_says_so(db, mgr):
    row = make_server(db, status=CONNECTED, tools_count=7)
    mgr.disconnect(db, row)
    assert row.status == DISCONNECTED and row.tools_count == 0
    assert event_types() == ["mcp.disconnected"]


def test_list_tools_is_none_when_not_connected(db, mgr):
    row = make_server(db)
    assert mgr.list_tools(row.id) is None
    assert mgr.cached_tools(row.id) == []


# ---------------------------------------------------------------------
# loss detection
# ---------------------------------------------------------------------

def test_crash_of_the_server_process_is_detected(db, mgr):
    row = make_server(db, extra_args=["--die-after", "1.5"])
    assert mgr.connect(db, row).state == CONNECTED

    def lost():
        with storage_db.SessionLocal() as s:
            return s.get(McpServer, row.id).status == ERROR

    assert wait_until(lost, timeout=20), "crash was never noticed"
    assert not mgr.is_connected(row.id)
    with storage_db.SessionLocal() as s:
        fresh = s.get(McpServer, row.id)
        ev = s.query(Event).filter(Event.event_type == "mcp.disconnected").one()
    assert "lost" in fresh.last_error
    assert ev.metadata_json["reason"] == "lost" and ev.metadata_json["server_id"] == row.id
    assert wait_until(lambda: not fixture_processes())


def test_can_reconnect_after_a_crash(db, mgr):
    row = make_server(db, extra_args=["--die-after", "1.5"])
    mgr.connect(db, row)
    assert wait_until(lambda: not mgr.is_connected(row.id), timeout=20)
    db.refresh(row)
    fixed = make_server(db, "fx2")  # same fixture, no crash flag -> proves a fresh connect works after a lost one
    assert mgr.connect(db, fixed).state == CONNECTED


# ---------------------------------------------------------------------
# failure paths
# ---------------------------------------------------------------------

def test_server_that_exits_immediately_is_a_failed_connect_not_an_exception(db, mgr):
    row = make_server(db, extra_args=["--exit-now"])
    t0 = time.time()
    status = mgr.connect(db, row)
    assert status.state == ERROR and status.error
    assert time.time() - t0 < 10  # fails fast, doesn't wait out the connect timeout
    assert row.status == ERROR and row.last_error == status.error and row.tools_count == 0
    assert not mgr.is_connected(row.id)
    assert event_types() == ["mcp.connect_failed"]  # NOT mcp.disconnected: nothing was connected


def test_missing_executable_is_a_failed_connect(db, mgr):
    row = make_server(db, command="definitely-not-a-real-launcher-xyz")
    status = mgr.connect(db, row)
    assert status.state == ERROR
    assert "FileNotFoundError" in status.error or "No such file" in status.error
    assert event_types() == ["mcp.connect_failed"]


def test_hanging_server_hits_the_connect_timeout_and_is_cleaned_up(db):
    m = McpManager(connect_timeout=1.5, ping_interval=0.4, ping_timeout=2)
    try:
        row = make_server(db, extra_args=["--hang"])
        t0 = time.time()
        status = m.connect(db, row)
        assert status.state == ERROR and "timed out" in status.error
        assert time.time() - t0 < 20
        assert not m.is_connected(row.id)
        assert wait_until(lambda: not fixture_processes(), timeout=15), "hung child left behind"
        assert event_types() == ["mcp.connect_failed"]
    finally:
        m.shutdown()


def test_failed_connect_can_be_retried_successfully(db, mgr):
    row = make_server(db, extra_args=["--exit-now"])
    assert mgr.connect(db, row).state == ERROR
    row.args_json = {"args": [FIXTURE]}
    db.commit()
    assert mgr.connect(db, row).state == CONNECTED
    assert row.status == CONNECTED and row.last_error is None


def test_concurrent_connect_of_the_same_server_is_rejected_not_doubled(db):
    m = McpManager(connect_timeout=4, ping_interval=0.4, ping_timeout=2)
    try:
        row = make_server(db, extra_args=["--hang"])
        first: dict = {}

        def slow_connect():
            s = storage_db.SessionLocal()
            try:
                first["status"] = m.connect(s, s.get(McpServer, row.id))
            finally:
                s.close()

        t = threading.Thread(target=slow_connect)
        t.start()
        assert wait_until(lambda: m._busy, timeout=5)
        with pytest.raises(McpBusyError):
            m.connect(db, row)
        with pytest.raises(McpBusyError):
            m.disconnect(db, row)
        t.join(30)
        assert first["status"].state == ERROR
        assert not m._busy  # claim released even after failure
    finally:
        m.shutdown()


# ---------------------------------------------------------------------
# transport wiring / secrets (patched transport)
# ---------------------------------------------------------------------

def test_stdio_config_reaches_the_sdk_with_argv_and_env(db, mgr, monkeypatch):
    import mcp.client.stdio as stdio_mod

    seen = {}

    def fake_stdio_client(params, errlog=None):
        seen["params"] = params
        raise RuntimeError("stop here")

    monkeypatch.setattr(stdio_mod, "stdio_client", fake_stdio_client)
    row = make_server(db, command="npx", env={"API_KEY": "abcdef123"})
    row.args_json = {"args": ["-y", "@scope/pkg", "--flag=a b"]}
    db.commit()
    mgr.connect(db, row)
    p = seen["params"]
    assert p.command == "npx"
    assert p.args == ["-y", "@scope/pkg", "--flag=a b"]  # kept as ONE argv entry each, never re-split
    assert p.env == {"API_KEY": "abcdef123"}


@pytest.mark.parametrize("transport,module,func", [
    ("http", "mcp.client.streamable_http", "streamablehttp_client"),
    ("sse", "mcp.client.sse", "sse_client"),
])
def test_http_and_sse_get_url_and_headers(db, mgr, monkeypatch, transport, module, func):
    import importlib

    mod = importlib.import_module(module)
    seen = {}

    def fake_client(url, headers=None, **kw):
        seen.update(url=url, headers=headers)
        raise RuntimeError("stop here")

    monkeypatch.setattr(mod, func, fake_client)
    row = McpServer(name="remote", transport=transport, url="http://127.0.0.1:1/mcp", headers_json={"Authorization": "Bearer tok-123456"})
    db.add(row)
    db.commit()
    assert mgr.connect(db, row).state == ERROR
    assert seen == {"url": "http://127.0.0.1:1/mcp", "headers": {"Authorization": "Bearer tok-123456"}}


def test_secret_values_never_leak_into_errors_or_events(db, mgr, monkeypatch):
    secret = "sk-live-supersecret-999"

    def boom(cfg):
        raise RuntimeError(f"upstream said: bad credentials {secret} rejected")

    monkeypatch.setattr(mgr, "_open_transport", boom)
    row = make_server(db, env={"API_KEY": secret})
    status = mgr.connect(db, row)
    assert status.state == ERROR
    assert secret not in status.error and "***" in status.error
    assert secret not in (row.last_error or "")
    with storage_db.SessionLocal() as s:
        assert all(secret not in str(e.metadata_json) for e in s.query(Event).all())


def test_header_secrets_are_redacted_too(db, mgr, monkeypatch):
    token = "Bearer zzz-header-secret"
    monkeypatch.setattr(mgr, "_open_transport", lambda cfg: (_ for _ in ()).throw(RuntimeError(f"401 for {token}")))
    row = McpServer(name="r", transport="http", url="http://127.0.0.1:1/x", headers_json={"Authorization": token})
    db.add(row)
    db.commit()
    assert token not in mgr.connect(db, row).error


def test_redact_helper_ignores_tiny_values_and_truncates():
    # the <4-char filter lives in secret_values(); _redact just replaces what it's given
    cfg = ServerConfig(id=1, name="n", transport="stdio", env={"A": "ab", "B": "longsecret"})
    assert cfg.secret_values() == ["longsecret"]  # "ab" is too short to be treated as a secret
    assert _redact("x longsecret y", cfg.secret_values()) == "x *** y"
    assert len(_redact("x" * 5000, [])) == 500


def test_flatten_exception_unwraps_groups():
    err = BaseExceptionGroup("g", [ValueError("inner one"), ExceptionGroup("h", [KeyError("k")])])
    text = _flatten_exception(err)
    assert "ValueError: inner one" in text and "KeyError" in text


# ---------------------------------------------------------------------
# startup / shutdown
# ---------------------------------------------------------------------

def test_reset_stale_statuses_only_touches_rows_claiming_to_be_live(db, mgr):
    a = make_server(db, "a", status=CONNECTED, tools_count=4)
    b = make_server(db, "b", status=CONNECTING)
    c = make_server(db, "c", status=ERROR, last_error="old failure")
    d = make_server(db, "d", status=DISCONNECTED)
    assert mgr.reset_stale_statuses(db) == 2
    for r in (a, b, c, d):
        db.refresh(r)
    assert (a.status, a.tools_count, b.status) == (DISCONNECTED, 0, DISCONNECTED)
    assert c.status == ERROR and c.last_error == "old failure"  # a failure the user hasn't seen yet is kept
    assert d.status == DISCONNECTED


def test_shutdown_terminates_every_child_and_is_repeatable(db):
    m = McpManager(connect_timeout=20, ping_interval=0.4, ping_timeout=3)
    a, b = make_server(db, "a"), make_server(db, "b")
    m.connect(db, a)
    m.connect(db, b)
    assert len(fixture_processes()) == 2
    m.shutdown()
    assert wait_until(lambda: not fixture_processes(), timeout=10)
    m.shutdown()  # second call: no error
    assert not m.is_connected(a.id)


def test_shutdown_before_any_connect_is_fine():
    McpManager().shutdown()


def test_loop_thread_is_lazy_and_reused(db, mgr):
    assert mgr._loop is None  # importing/creating the manager starts nothing
    row = make_server(db)
    mgr.connect(db, row)
    loop = mgr._loop
    mgr.disconnect(db, row)
    mgr.connect(db, row)
    assert mgr._loop is loop


# ---------------------------------------------------------------------
# call_tool -- against the real fixture server's echo/add/delete_file/fail/slow
# ---------------------------------------------------------------------

def test_call_tool_returns_the_result_as_text(db, mgr):
    row = make_server(db)
    mgr.connect(db, row)
    assert mgr.call_tool(row.id, "echo", {"text": "hello there"}) == "hello there"
    assert mgr.call_tool(row.id, "add", {"a": 2, "b": 3}) == "5"


def test_call_tool_reads_env_reaching_the_child(db, mgr):
    row = make_server(db, env={"FIXTURE_VAR": "present"})
    mgr.connect(db, row)
    assert mgr.call_tool(row.id, "env_value", {"name": "FIXTURE_VAR"}) == "present"


def test_call_tool_on_a_server_that_declared_iserror_raises(db, mgr):
    row = make_server(db)
    mgr.connect(db, row)
    with pytest.raises(RuntimeError, match="custom failure"):
        mgr.call_tool(row.id, "fail", {"message": "custom failure"})


def test_call_tool_on_disconnected_server_raises_without_attempting(db, mgr):
    row = make_server(db)
    with pytest.raises(RuntimeError, match="not connected"):
        mgr.call_tool(row.id, "echo", {"text": "x"})


def test_call_tool_on_unknown_server_id_raises(mgr):
    with pytest.raises(RuntimeError, match="not connected"):
        mgr.call_tool(999999, "echo", {"text": "x"})


def test_call_tool_times_out_on_a_slow_tool_without_hanging_the_caller(db):
    m = McpManager(connect_timeout=20, ping_interval=5, ping_timeout=3, tool_call_timeout=1)
    try:
        row = make_server(db)
        m.connect(db, row)
        t0 = time.time()
        with pytest.raises(RuntimeError):
            m.call_tool(row.id, "slow", {"seconds": 10})
        assert time.time() - t0 < 10  # the CLIENT gives up well before the server's 10s sleep finishes
    finally:
        m.shutdown()


def test_call_tool_error_does_not_leak_secrets(db, mgr):
    secret = "sk-call-tool-secret-555"
    row = make_server(db, env={"API_KEY": secret})
    mgr.connect(db, row)
    with pytest.raises(RuntimeError) as exc_info:
        mgr.call_tool(row.id, "fail", {"message": f"leaked {secret} in error"})
    assert secret not in str(exc_info.value) and "***" in str(exc_info.value)


def test_call_tool_after_disconnect_and_reconnect_works(db, mgr):
    row = make_server(db)
    mgr.connect(db, row)
    mgr.disconnect(db, row)
    mgr.connect(db, row)
    assert mgr.call_tool(row.id, "echo", {"text": "back"}) == "back"


def test_annotations_reach_cached_tools_dicts(db, mgr):
    row = make_server(db)
    mgr.connect(db, row)
    tools = {t["name"]: t for t in mgr.cached_tools(row.id)}
    assert tools["echo"]["annotations"]["readOnlyHint"] is True
    assert tools["delete_file"]["annotations"]["destructiveHint"] is True
    assert "annotations" not in tools["add"]  # no hints declared -> key omitted entirely
