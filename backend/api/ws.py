"""
backend/api/ws.py

Three WebSocket endpoints:
  - /ws/metrics -- periodic hardware/runtime snapshot push (unchanged).
  - /ws/events -- live tail of the structured event log (core/events/).
    Every event is also durably persisted (GET /api/v1/events is the
    history view of the same stream); this is the "watch it happen"
    view for a future Logs/Notifications panel.
  - /ws/agent-terminal/{agent_id} -- attaches to a real, live agent CLI
    process (Hermes today) running under a pseudo-terminal, so the
    browser gets the agent's actual interactive session -- its own
    banner, colors, prompts -- not a reimplementation of it. See
    core/agents/pty_session.py for the pty plumbing and
    core/agents/*_adapter.py for what "real CLI" means per agent.

Auth note: browsers cannot attach custom headers to a WebSocket handshake,
so the access token travels as a query parameter here (`?token=...`)
rather than the `Authorization` header the REST API uses.
"""

from __future__ import annotations

import asyncio
import json
import queue
from dataclasses import asdict

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ..core import agents as agent_backends
from ..core import events as events_module
from ..core.agents.pty_session import PtySession
from ..core.runtime_manager import runtime_manager
from ..core.security import get_or_create_access_token
from ..storage.db import Agent, MLModel, Runtime, SessionLocal

router = APIRouter()

_ACCESS_TOKEN = get_or_create_access_token()
PUSH_INTERVAL_SECONDS = 1.5
TERMINAL_READ_POLL_SECONDS = 0.05


def _build_snapshot() -> dict:
    hw = runtime_manager.get_hardware_snapshot()
    with SessionLocal() as db:
        runtime_payload = []
        for rt in db.query(Runtime).all():
            status = runtime_manager.get_status(db, rt)
            metrics = runtime_manager.get_metrics(rt)
            runtime_payload.append(
                {
                    "runtime_id": rt.id,
                    "name": rt.name,
                    "state": status.state.value,
                    "tokens_per_sec": metrics.tokens_per_sec,
                    "active_slots": metrics.active_slots,
                }
            )

    return {
        "cpu": asdict(hw["cpu"]),
        "memory": asdict(hw["memory"]),
        "gpu": asdict(hw["gpu"]),
        "runtimes": runtime_payload,
    }


@router.websocket("/ws/metrics")
async def metrics_ws(websocket: WebSocket, token: str = Query(default="")):
    if token != _ACCESS_TOKEN:
        await websocket.close(code=4401)  # app-level "unauthorized"
        return

    await websocket.accept()
    try:
        while True:
            # NOTE: _build_snapshot() does blocking DB/HTTP calls. Fine at
            # MVP scale (one user, a handful of runtimes); if that ever
            # becomes a bottleneck, wrap it in asyncio.to_thread() rather
            # than rewriting this loop.
            snapshot = _build_snapshot()
            await websocket.send_text(json.dumps(snapshot))
            await asyncio.sleep(PUSH_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        pass


EVENTS_QUEUE_POLL_SECONDS = 0.5


@router.websocket("/ws/events")
async def events_ws(websocket: WebSocket, token: str = Query(default="")):
    """Live tail of core/events/. Every event is already durably
    persisted by the time it reaches here (EventBus.emit() persists
    before it publishes), so a client that reconnects after a gap should
    backfill with GET /api/v1/events?before_id=... rather than expect
    this socket to replay anything it missed -- this is a live feed, not
    a queue with delivery guarantees."""
    if token != _ACCESS_TOKEN:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    sub_id, q = events_module.event_bus.subscribe()
    try:
        loop = asyncio.get_event_loop()
        while True:
            # queue.Queue.get() is blocking, so it runs off the event
            # loop's thread (same run_in_executor pattern the
            # agent-terminal pump below uses) -- a websocket that never
            # sends anything must not stall this endpoint from noticing
            # the client disconnected.
            ev = await loop.run_in_executor(None, _poll_one, q)
            if ev is not None:
                await websocket.send_text(json.dumps(ev.to_dict()))
    except WebSocketDisconnect:
        pass
    finally:
        events_module.event_bus.unsubscribe(sub_id)


def _poll_one(q: "queue.Queue"):
    try:
        return q.get(timeout=EVENTS_QUEUE_POLL_SECONDS)
    except queue.Empty:
        return None


@router.websocket("/ws/agent-terminal/{agent_id}")
async def agent_terminal_ws(
    websocket: WebSocket,
    agent_id: int,
    token: str = Query(default=""),
    runtime_id: int = Query(...),
    cols: int = Query(default=80),
    rows: int = Query(default=24),
):
    if token != _ACCESS_TOKEN:
        await websocket.close(code=4401)
        return

    with SessionLocal() as db:
        agent = db.get(Agent, agent_id)
        runtime = db.get(Runtime, runtime_id)
        model_name = None
        if runtime is not None and runtime.model_id is not None:
            model = db.get(MLModel, runtime.model_id)
            model_name = model.name if model else None

    await websocket.accept()

    if agent is None or runtime is None:
        await websocket.send_text(json.dumps({"type": "error", "message": "agent یا runtime پیدا نشد."}))
        await websocket.close(code=4404)
        return

    if agent.agent_backend == "generic":
        await websocket.send_text(
            json.dumps({"type": "error", "message": "این agent یه backend واقعی نداره — اول یکی رو توی تنظیماتش انتخاب کن."})
        )
        await websocket.close(code=4400)
        return

    adapter = agent_backends.get_adapter(agent.agent_backend)
    if adapter is None:
        await websocket.send_text(json.dumps({"type": "error", "message": f"backend '{agent.agent_backend}' شناخته‌شده نیست."}))
        await websocket.close(code=4400)
        return

    base_url = f"http://{runtime.host}:{runtime.port}/v1"

    # Auto-configure every time a terminal session opens -- idempotent
    # (re-running `hermes config set` with the same values is harmless),
    # and guarantees the agent is actually pointed at *this* runtime even
    # if it was last configured against a different one.
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, adapter.configure, base_url, "sk-no-key", model_name
        )
    except RuntimeError as exc:
        await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
        await websocket.close(code=4400)
        return

    try:
        session = PtySession(adapter.build_launch_command(), cols=cols, rows=rows)
    except RuntimeError as exc:
        await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
        await websocket.close(code=4400)
        return

    async def pump_output() -> None:
        loop = asyncio.get_event_loop()
        while True:
            chunk = await loop.run_in_executor(None, session.read, 65536, TERMINAL_READ_POLL_SECONDS)
            if chunk:
                await websocket.send_text(
                    json.dumps({"type": "output", "data": chunk.decode("utf-8", errors="replace")})
                )
            elif not session.is_alive():
                await websocket.send_text(json.dumps({"type": "exit"}))
                return
            else:
                await asyncio.sleep(0.01)

    pump_task = asyncio.create_task(pump_output())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") == "input":
                session.write(msg.get("data", "").encode("utf-8"))
            elif msg.get("type") == "resize":
                session.resize(int(msg.get("cols", cols)), int(msg.get("rows", rows)))
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        session.close()
