"""
backend/core/agent_loop.py

Turns core/chat.py's single request/response proxy into a real agent
loop (completing Phase 4): the model can ask to call a tool, the call
is gated by the Permission Engine (core/permissions/), the owning MCP
server actually runs it (core/mcp/manager.py), and the result goes back
into the conversation for another model turn -- up to `max_steps` tool
round-trips before the loop stops and returns whatever it has, so a
model that keeps asking for tools can't run forever.

Only tools the chat's agent was attached to (`agent.config_json
["mcp_server_ids"]`, set via `PATCH /api/v1/agents/{id}` like every
other agent-config field -- see api/http.py) AND whose server is
currently CONNECTED are offered to the model each turn; nothing here
tries to connect a server on the model's behalf. A call the model makes
to a name outside that list -- or that that turn started with -- fails
as "unknown tool", never attempted.

Every completion call and every tool call is its own DB-committed
ChatMessage as it happens (`role="assistant"` with `tool_meta_json.
calls` for a step that asked for tools, `role="tool"` for each
result) -- a turn that fails partway through (a completion timeout, an
unanswered permission request) still leaves everything that DID happen
on the record, same as the pre-agent-loop code already did for the
user's own message.
"""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.orm import Session

from . import chat as chat_engine
from . import events
from .mcp import mcp_manager
from .permissions import mcp_tool_scope, permission_engine
from ..storage import db as storage_db
from ..storage.db import Agent, Chat, ChatMessage, McpServer, Tool

MAX_STEPS = 8
DEFAULT_PERMISSION_TIMEOUT = 120.0
_QUALIFIED_SEP = "__"


def _agent_tools(db: Session, agent: Optional[Agent]) -> list[Tool]:
    """Enabled tools from the agent's attached, currently-connected MCP
    servers -- nothing offered here can be stale (a disconnected
    server's tools simply aren't listed) or something the user never
    opted this agent into."""
    if agent is None:
        return []
    server_ids = (agent.config_json or {}).get("mcp_server_ids") or []
    if not server_ids:
        return []
    connected_ids = [
        s.id
        for s in db.query(McpServer).filter(McpServer.id.in_(server_ids)).all()
        if mcp_manager.is_connected(s.id)
    ]
    if not connected_ids:
        return []
    return (
        db.query(Tool)
        .filter(Tool.mcp_server_id.in_(connected_ids), Tool.enabled.is_(True))
        .order_by(Tool.mcp_server_id, Tool.name)
        .all()
    )


def _qualified_name(server_id: int, tool_name: str) -> str:
    """The model sees one flat namespace, so two servers exposing a
    same-named tool (e.g. both have "search") can't collide -- the
    server id is folded into the name the model is given and must echo
    back in its tool call."""
    return f"{server_id}{_QUALIFIED_SEP}{tool_name}"


def _tool_spec(tool: Tool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": _qualified_name(tool.mcp_server_id, tool.name),
            "description": tool.description or "",
            "parameters": tool.input_schema_json or {"type": "object", "properties": {}},
        },
    }


def _history_for_completion(db: Session, chat_id: int) -> list[dict]:
    """The stored ChatMessage rows, translated to what llama-server's
    /v1/chat/completions expects on the wire -- an assistant step that
    asked for tools carries `tool_calls`; a tool result carries
    `tool_call_id` so the model can match it to its own request."""
    out = []
    for m in storage_db.get_chat_messages(db, chat_id):
        entry: dict = {"role": m.role, "content": m.content}
        meta = m.tool_meta_json or {}
        if m.role == "assistant" and meta.get("calls"):
            entry["tool_calls"] = [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments") or {})},
                }
                for c in meta["calls"]
            ]
        elif m.role == "tool":
            entry["tool_call_id"] = meta.get("call_id")
        out.append(entry)
    return out


def run_agent_turn(
    db: Session,
    chat: Chat,
    endpoint: str,
    *,
    temperature: float = 0.8,
    max_tokens: Optional[int] = None,
    max_steps: int = MAX_STEPS,
    permission_timeout: float = DEFAULT_PERMISSION_TIMEOUT,
) -> list[ChatMessage]:
    """Runs one user turn to completion. The caller has already written
    the user's ChatMessage; this writes and returns every message from
    here on (at least one -- the model's reply -- more if tools were
    called). Raises chat_engine.ChatCompletionError, same as the plain
    proxy did, if any completion call in the turn fails; messages
    already written before that point stay committed."""
    agent = db.get(Agent, chat.agent_id) if chat.agent_id is not None else None
    tools = _agent_tools(db, agent)
    tool_specs = [_tool_spec(t) for t in tools] or None
    tools_by_name = {_qualified_name(t.mcp_server_id, t.name): t for t in tools}
    session_link = (
        storage_db.get_active_session_link(db, chat.runtime_id, chat.agent_id)
        if chat.agent_id is not None
        else None
    )

    written: list[ChatMessage] = []
    steps = 0

    while True:
        events.emit(
            events.EventType.INFERENCE_STARTED,
            runtime_id=chat.runtime_id,
            agent_id=chat.agent_id,
            metadata={"chat_id": chat.id},
        )
        try:
            result = chat_engine.send_chat_completion(
                endpoint, _history_for_completion(db, chat.id), temperature=temperature, max_tokens=max_tokens,
                tools=tool_specs,
            )
        except chat_engine.ChatCompletionError as exc:
            events.emit(
                events.EventType.INFERENCE_FAILED,
                runtime_id=chat.runtime_id,
                agent_id=chat.agent_id,
                metadata={"chat_id": chat.id, "error": str(exc)},
            )
            raise

        tool_calls = result.get("tool_calls") or []
        assistant_meta = {"calls": tool_calls} if tool_calls else {}
        assistant_message = storage_db.add_chat_message(
            db,
            chat.id,
            role="assistant",
            content=result.get("content") or "",
            prompt_tokens=result.get("prompt_tokens"),
            completion_tokens=result.get("completion_tokens"),
            latency_ms=result.get("latency_ms"),
            tool_meta=assistant_meta,
        )
        written.append(assistant_message)

        tokens_per_sec = None
        if result.get("completion_tokens") and result.get("latency_ms"):
            tokens_per_sec = result["completion_tokens"] / (result["latency_ms"] / 1000)
        storage_db.record_request(
            db,
            runtime_id=chat.runtime_id,
            agent_id=chat.agent_id,
            prompt_tokens=result.get("prompt_tokens") or 0,
            completion_tokens=result.get("completion_tokens") or 0,
            latency_ms=result.get("latency_ms"),
            tokens_per_sec=tokens_per_sec,
            session_link_id=session_link.id if session_link is not None else None,
            chat_id=chat.id,
        )
        events.emit(
            events.EventType.INFERENCE_COMPLETED,
            runtime_id=chat.runtime_id,
            agent_id=chat.agent_id,
            session_id=session_link.id if session_link is not None else None,
            metadata={
                "chat_id": chat.id,
                "prompt_tokens": result.get("prompt_tokens"),
                "completion_tokens": result.get("completion_tokens"),
                "latency_ms": result.get("latency_ms"),
            },
        )

        if not tool_calls:
            return written

        if steps >= max_steps:
            events.emit(
                events.EventType.TOOL_FAILED,
                agent_id=chat.agent_id,
                runtime_id=chat.runtime_id,
                metadata={"chat_id": chat.id, "reason": "max_steps_exceeded", "requested": len(tool_calls)},
            )
            for call in tool_calls:
                written.append(_tool_result_message(db, chat.id, call, "step limit reached before this tool ran", "error"))
            return written

        for call in tool_calls:
            steps += 1
            written.append(_execute_tool_call(db, chat, call, tools_by_name, permission_timeout))
            if steps >= max_steps and call is not tool_calls[-1]:
                remaining = tool_calls[tool_calls.index(call) + 1 :]
                for skipped in remaining:
                    written.append(_tool_result_message(db, chat.id, skipped, "step limit reached before this tool ran", "error"))
                return written
        # loop again: the model sees every tool result and takes the next step


def _tool_result_message(
    db: Session, chat_id: int, call: dict, content: str, status: str, *, tool: Optional[Tool] = None, decision: Optional[dict] = None
) -> ChatMessage:
    meta = {
        "call_id": call.get("id"),
        "name": call.get("name"),
        "mcp_server_id": tool.mcp_server_id if tool is not None else None,
        "status": status,
    }
    if tool is not None:
        meta["risk_level"] = tool.risk_level
    if decision is not None:
        meta["decision"] = decision.get("decision_kind") or decision.get("decision")
    return storage_db.add_chat_message(db, chat_id, role="tool", content=content, tool_meta=meta)


def _execute_tool_call(
    db: Session, chat: Chat, call: dict, tools_by_name: dict[str, Tool], permission_timeout: float
) -> ChatMessage:
    qualified = call.get("name") or ""
    tool = tools_by_name.get(qualified)

    events.emit(
        events.EventType.TOOL_CALLED,
        agent_id=chat.agent_id,
        runtime_id=chat.runtime_id,
        metadata={"chat_id": chat.id, "tool": qualified, "arguments": call.get("arguments")},
    )

    if tool is None:
        events.emit(
            events.EventType.TOOL_FAILED,
            agent_id=chat.agent_id,
            runtime_id=chat.runtime_id,
            metadata={"chat_id": chat.id, "tool": qualified, "reason": "unknown_tool"},
        )
        return _tool_result_message(db, chat.id, call, f"unknown tool: {qualified}", "error")

    if not mcp_manager.is_connected(tool.mcp_server_id):
        events.emit(
            events.EventType.TOOL_FAILED,
            agent_id=chat.agent_id,
            runtime_id=chat.runtime_id,
            metadata={"chat_id": chat.id, "tool": qualified, "reason": "server_not_connected"},
        )
        return _tool_result_message(db, chat.id, call, f"the server for {tool.name} is not connected", "error", tool=tool)

    scope_type, scope_key = mcp_tool_scope(tool.mcp_server_id, tool.name)
    decision = permission_engine.check_or_request(
        db,
        scope_type=scope_type,
        scope_key=scope_key,
        risk_level=tool.risk_level,
        agent_id=chat.agent_id,
        description=f"{tool.name}({json.dumps(call.get('arguments') or {}, ensure_ascii=False)})",
        timeout=permission_timeout,
    )
    if decision["decision"] != "allow":
        events.emit(
            events.EventType.TOOL_FAILED,
            agent_id=chat.agent_id,
            runtime_id=chat.runtime_id,
            metadata={"chat_id": chat.id, "tool": qualified, "reason": "permission_denied"},
        )
        return _tool_result_message(db, chat.id, call, "permission denied", "denied", tool=tool, decision=decision)

    try:
        output = mcp_manager.call_tool(tool.mcp_server_id, tool.name, call.get("arguments") or {})
    except Exception as exc:  # noqa: BLE001 - reported into the transcript, never crashes the turn
        events.emit(
            events.EventType.TOOL_FAILED,
            agent_id=chat.agent_id,
            runtime_id=chat.runtime_id,
            metadata={"chat_id": chat.id, "tool": qualified, "reason": "call_failed", "error": str(exc)},
        )
        return _tool_result_message(db, chat.id, call, f"tool error: {exc}", "error", tool=tool, decision=decision)

    events.emit(
        events.EventType.TOOL_COMPLETED,
        agent_id=chat.agent_id,
        runtime_id=chat.runtime_id,
        metadata={"chat_id": chat.id, "tool": qualified},
    )
    return _tool_result_message(db, chat.id, call, output, "ok", tool=tool, decision=decision)
