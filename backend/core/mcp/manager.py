"""
backend/core/mcp/manager.py

MCP Manager (master build prompt Phase 5): owns the LIVE half of every
configured MCP server -- the client session to a running server -- while
the `mcp_servers` row (storage/db.py: McpServer) owns the durable half.
Same split, and same discipline, as runtime_manager.py: this is the ONE
place that opens an MCP client connection or mirrors its state back onto
the DB row, and it's the one place that emits mcp.* events.

Why a background event loop thread
----------------------------------
The official `mcp` client is async and built on anyio task groups, which
must be entered and exited *in the same task*. FastAPI's sync route
handlers run on a thread pool, so they can't hold such a context across
calls. Instead the manager runs one private asyncio loop in a daemon
thread, and every server connection is ONE long-lived task on that loop
(`_run`): it opens the transport + session, then just supervises it until
told to stop -- so enter and exit always happen in the same task. Sync
callers hop onto the loop with `run_coroutine_threadsafe`.

Loss detection
--------------
A server process that dies (or an HTTP server that goes away) doesn't
tell anyone. While connected, `_run` sends an MCP `ping` every
`ping_interval` seconds; a failed/timed-out ping ends the task, and
`_on_lost` flips the row to ERROR and emits mcp.disconnected with
`reason: "lost"` -- the MCP analogue of runtime_manager's poll-based
crash detection.

Deliberately NOT here: calling tools. Executing a tool is Tool Registry
+ Permission Engine territory (the next phases); until a permission layer
exists there is no code path that invokes a server's tools. This module
only connects, lists tools, and disconnects.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.orm import Session

from .. import events
from ...storage.db import McpServer

DEFAULT_CONNECT_TIMEOUT = 60.0  # generous: `npx -y <pkg>` may download on first run
DEFAULT_PING_INTERVAL = 15.0
DEFAULT_PING_TIMEOUT = 10.0
STOP_GRACE_SECONDS = 10.0
MAX_ERROR_LEN = 500

# DB row status values (CHECK constraint in db.py mirrors these).
DISCONNECTED = "DISCONNECTED"
CONNECTING = "CONNECTING"
CONNECTED = "CONNECTED"
ERROR = "ERROR"


class McpBusyError(RuntimeError):
    """A connect/disconnect for this server is already in progress."""


@dataclass(frozen=True)
class ServerConfig:
    """Immutable snapshot of what's needed to connect -- taken from the DB
    row on the calling thread so the loop thread never touches an ORM
    object that belongs to a request's session."""

    id: int
    name: str
    transport: str
    command: Optional[str] = None
    args: tuple[str, ...] = ()
    env: dict = field(default_factory=dict)
    url: Optional[str] = None
    headers: dict = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: McpServer) -> "ServerConfig":
        return cls(
            id=row.id,
            name=row.name,
            transport=row.transport,
            command=row.command,
            args=tuple((row.args_json or {}).get("args", [])),
            env=dict(row.env_json or {}),
            url=row.url,
            headers=dict(row.headers_json or {}),
        )

    def secret_values(self) -> list[str]:
        vals = [v for v in list(self.env.values()) + list(self.headers.values()) if isinstance(v, str)]
        return [v for v in vals if len(v) >= 4]  # a 1-3 char "secret" would shred unrelated words


@dataclass
class McpStatus:
    state: str
    tools_count: int = 0
    error: Optional[str] = None
    server_info: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "tools_count": self.tools_count,
            "error": self.error,
            "server_info": self.server_info,
        }


@dataclass
class _Live:
    cfg: ServerConfig
    stop_event: asyncio.Event
    task: Optional["asyncio.Task[None]"] = None
    session: Any = None
    tools: list[dict] = field(default_factory=list)
    server_info: dict = field(default_factory=dict)
    stop_requested: bool = False


def _flatten_exception(exc: BaseException) -> str:
    """anyio task groups wrap failures in (Base)ExceptionGroup; the useful
    message is the innermost leaf."""
    if isinstance(exc, BaseExceptionGroup):
        leaves = [_flatten_exception(e) for e in exc.exceptions]
        return "; ".join(dict.fromkeys(leaves))
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        text = text.replace(s, "***")
    return text[:MAX_ERROR_LEN]


def _tool_to_dict(tool: Any) -> dict:
    out = {
        "name": tool.name,
        "description": getattr(tool, "description", None) or "",
        "input_schema": getattr(tool, "inputSchema", None) or {},
    }
    title = getattr(tool, "title", None)
    if title:
        out["title"] = title
    ann = getattr(tool, "annotations", None)
    if ann is not None:
        out["annotations"] = ann.model_dump(exclude_none=True) if hasattr(ann, "model_dump") else dict(ann)
    return out


class McpManager:
    def __init__(
        self,
        *,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        ping_interval: float = DEFAULT_PING_INTERVAL,
        ping_timeout: float = DEFAULT_PING_TIMEOUT,
    ) -> None:
        self.connect_timeout = connect_timeout
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._loop_lock = threading.Lock()

        self._live: dict[int, _Live] = {}  # touched only from the loop thread
        self._busy: set[int] = set()  # server ids with a connect/disconnect in flight
        self._state_lock = threading.Lock()

    # ------------------------------------------------------------------
    # loop thread plumbing
    # ------------------------------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            loop = asyncio.new_event_loop()
            started = threading.Event()

            def _target() -> None:
                asyncio.set_event_loop(loop)
                loop.call_soon(started.set)
                loop.run_forever()

            thread = threading.Thread(target=_target, name="mcp-manager-loop", daemon=True)
            thread.start()
            started.wait(5)
            self._loop, self._thread = loop, thread
            return loop

    def _call(self, coro: Any, timeout: Optional[float] = None) -> Any:
        loop = self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    # ------------------------------------------------------------------
    # transport (overridable seam: tests patch this for http/sse)
    # ------------------------------------------------------------------

    def _open_transport(self, cfg: ServerConfig):
        """Returns an async context manager yielding (read, write, ...).
        Imported lazily so a missing/incompatible `mcp` package only
        breaks MCP connects, not the whole app at import time."""
        if cfg.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=cfg.command or "",
                args=list(cfg.args),
                env=dict(cfg.env) or None,
            )
            return stdio_client(params, errlog=sys.stderr)
        if cfg.transport == "http":
            from mcp.client.streamable_http import streamablehttp_client

            return streamablehttp_client(cfg.url or "", headers=dict(cfg.headers) or None)
        if cfg.transport == "sse":
            from mcp.client.sse import sse_client

            return sse_client(cfg.url or "", headers=dict(cfg.headers) or None)
        raise ValueError(f"unknown transport {cfg.transport!r}")

    # ------------------------------------------------------------------
    # the per-server task (runs on the loop thread)
    # ------------------------------------------------------------------

    async def _list_all_tools(self, session: Any) -> list[dict]:
        tools: list[dict] = []
        cursor: Optional[str] = None
        for _ in range(100):  # hard stop against a server that paginates forever
            result = await session.list_tools(cursor)
            tools.extend(_tool_to_dict(t) for t in result.tools)
            cursor = getattr(result, "nextCursor", None)
            if not cursor:
                break
        return tools

    async def _run(self, live: _Live, ready: "asyncio.Future[None]") -> None:
        from mcp import ClientSession

        lost_error: Optional[str] = None
        try:
            async with self._open_transport(live.cfg) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    init = await session.initialize()
                    live.tools = await self._list_all_tools(session)
                    info = getattr(init, "serverInfo", None)
                    live.server_info = {
                        "name": getattr(info, "name", None),
                        "version": getattr(info, "version", None),
                        "protocol_version": str(getattr(init, "protocolVersion", "") or "") or None,
                    }
                    live.session = session
                    if not ready.done():
                        ready.set_result(None)
                    lost_error = await self._supervise(live, session)
        except asyncio.CancelledError:
            if not ready.done():
                ready.cancel()
            raise
        except BaseException as exc:  # noqa: BLE001 - reported, never swallowed silently
            message = _redact(_flatten_exception(exc), live.cfg.secret_values())
            if not ready.done():
                ready.set_exception(RuntimeError(message))
            else:
                lost_error = message
        finally:
            live.session = None
            if self._live.get(live.cfg.id) is live:
                del self._live[live.cfg.id]
            if lost_error is not None and not live.stop_requested:
                # Off-loop: touches the DB (blocking) and emits an event.
                with contextlib.suppress(Exception):
                    await asyncio.get_running_loop().run_in_executor(None, self._on_lost, live.cfg, lost_error)

    async def _supervise(self, live: _Live, session: Any) -> Optional[str]:
        """Waits for a stop request while pinging. Returns None on a
        requested stop, or an error message when the link is lost."""
        while True:
            try:
                await asyncio.wait_for(live.stop_event.wait(), timeout=self.ping_interval)
                return None
            except asyncio.TimeoutError:
                pass
            try:
                await asyncio.wait_for(session.send_ping(), timeout=self.ping_timeout)
            except BaseException as exc:  # noqa: BLE001
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return _redact(f"connection lost ({_flatten_exception(exc)})", live.cfg.secret_values())

    def _on_lost(self, cfg: ServerConfig, error: str) -> None:
        from ...storage import db as storage_db

        try:
            db = storage_db.SessionLocal()
            try:
                row = db.get(McpServer, cfg.id)
                if row is not None and row.status == CONNECTED:
                    row.status = ERROR
                    row.last_error = error
                    db.commit()
            finally:
                db.close()
        except Exception as exc:  # the DB must not be able to hide the event below
            print(f"[mcp] could not record loss of {cfg.name}: {exc}", file=sys.stderr)
        events.emit(
            events.EventType.MCP_DISCONNECTED,
            metadata={"server_id": cfg.id, "name": cfg.name, "transport": cfg.transport, "reason": "lost", "error": error},
        )

    # ------------------------------------------------------------------
    # coroutines the sync API hops onto the loop to run
    # ------------------------------------------------------------------

    async def _start(self, cfg: ServerConfig) -> _Live:
        live = _Live(cfg=cfg, stop_event=asyncio.Event())
        loop = asyncio.get_running_loop()
        ready: "asyncio.Future[None]" = loop.create_future()
        self._live[cfg.id] = live
        live.task = loop.create_task(self._run(live, ready), name=f"mcp-{cfg.id}")
        try:
            await asyncio.wait_for(asyncio.shield(ready), timeout=self.connect_timeout)
        except asyncio.TimeoutError:
            await self._halt(live)
            raise RuntimeError(f"connect timed out after {self.connect_timeout:g}s") from None
        except BaseException:
            await self._halt(live)
            raise
        return live

    async def _halt(self, live: _Live) -> None:
        """Ask the task to finish cleanly, then cancel it if it doesn't."""
        live.stop_requested = True
        live.stop_event.set()
        task = live.task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=STOP_GRACE_SECONDS)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    async def _stop(self, server_id: int) -> bool:
        live = self._live.get(server_id)
        if live is None:
            return False
        await self._halt(live)
        return True

    async def _refresh_tools(self, server_id: int) -> Optional[list[dict]]:
        live = self._live.get(server_id)
        if live is None or live.session is None:
            return None
        live.tools = await asyncio.wait_for(self._list_all_tools(live.session), timeout=self.ping_timeout + 20)
        return live.tools

    async def _stop_all(self) -> None:
        for live in list(self._live.values()):
            await self._halt(live)

    # ------------------------------------------------------------------
    # sync API used by api/mcp.py
    # ------------------------------------------------------------------

    def is_connected(self, server_id: int) -> bool:
        live = self._live.get(server_id)
        return live is not None and live.session is not None

    def _claim(self, server_id: int) -> None:
        with self._state_lock:
            if server_id in self._busy:
                raise McpBusyError("یه عملیات دیگه روی این سرور در حال انجامه.")
            self._busy.add(server_id)

    def _release(self, server_id: int) -> None:
        with self._state_lock:
            self._busy.discard(server_id)

    def connect(self, db: Session, server: McpServer) -> McpStatus:
        """Connect (idempotent: an already-connected server just reports
        its state). Blocks up to `connect_timeout`. Never raises for a
        failed connection -- that's a normal outcome, returned as
        state=ERROR with the reason (and emitted as mcp.connect_failed)."""
        self._claim(server.id)
        try:
            if self.is_connected(server.id):
                return McpStatus(CONNECTED, server.tools_count, None, dict(server.server_info_json or {}))

            cfg = ServerConfig.from_row(server)
            server.status = CONNECTING
            server.last_error = None
            db.commit()

            try:
                live = self._call(self._start(cfg), timeout=self.connect_timeout + STOP_GRACE_SECONDS + 5)
            except BaseException as exc:  # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                message = _redact(_flatten_exception(exc) if not isinstance(exc, RuntimeError) else str(exc), cfg.secret_values())
                server.status = ERROR
                server.last_error = message
                server.tools_count = 0
                db.commit()
                events.emit(
                    events.EventType.MCP_CONNECT_FAILED,
                    metadata={"server_id": server.id, "name": server.name, "transport": server.transport, "error": message},
                )
                return McpStatus(ERROR, 0, message)

            server.status = CONNECTED
            server.last_error = None
            server.tools_count = len(live.tools)
            server.server_info_json = dict(live.server_info)
            db.commit()
            events.emit(
                events.EventType.MCP_CONNECTED,
                metadata={
                    "server_id": server.id,
                    "name": server.name,
                    "transport": server.transport,
                    "tools_count": len(live.tools),
                    "server_name": live.server_info.get("name"),
                    "server_version": live.server_info.get("version"),
                },
            )
            return McpStatus(CONNECTED, len(live.tools), None, dict(live.server_info))
        finally:
            self._release(server.id)

    def disconnect(self, db: Session, server: McpServer) -> McpStatus:
        """Idempotent. Also clears a stale CONNECTED/ERROR row when nothing
        is actually live (e.g. after a lost connection)."""
        self._claim(server.id)
        try:
            was_live = self._call(self._stop(server.id), timeout=STOP_GRACE_SECONDS * 2 + 5) if self._loop else False
            was_marked_connected = server.status == CONNECTED
            server.status = DISCONNECTED
            server.last_error = None
            server.tools_count = 0
            db.commit()
            if was_live or was_marked_connected:
                events.emit(
                    events.EventType.MCP_DISCONNECTED,
                    metadata={"server_id": server.id, "name": server.name, "transport": server.transport, "reason": "requested"},
                )
            return McpStatus(DISCONNECTED)
        finally:
            self._release(server.id)

    def list_tools(self, server_id: int) -> Optional[list[dict]]:
        """Fresh `tools/list` from the live session; None when the server
        isn't connected. A failure to list is raised to the caller."""
        if not self.is_connected(server_id):
            return None
        return self._call(self._refresh_tools(server_id), timeout=self.ping_timeout + 25)

    def cached_tools(self, server_id: int) -> list[dict]:
        live = self._live.get(server_id)
        return list(live.tools) if live else []

    def reset_stale_statuses(self, db: Session) -> int:
        """At process start nothing is live, so any CONNECTED/CONNECTING
        rows left by the previous run are lies -- reset them. Returns the
        number of rows fixed."""
        rows = db.query(McpServer).filter(McpServer.status.in_([CONNECTED, CONNECTING])).all()
        for row in rows:
            row.status = DISCONNECTED
            row.tools_count = 0
        if rows:
            db.commit()
        return len(rows)

    def shutdown(self) -> None:
        """Stops every live connection (terminating stdio child processes)
        and the loop thread. Safe to call more than once."""
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(self._stop_all(), loop).result(STOP_GRACE_SECONDS * 2 + 5)
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None


# One control-center process, one manager -- same reasoning as
# runtime_manager's module-level singleton.
mcp_manager = McpManager()
