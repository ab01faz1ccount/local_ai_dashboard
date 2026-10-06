"""
backend/core/session_export.py

Exports one `llm_agent_sessions` link (section 22's requirement) as
JSON, JSONL, or Markdown. Doubles as a stand-in for Agent Trace
(section 17) until that gets its own timeline UI, since both are
"make sense of what an agent actually did" views over the same data --
the `events` table filtered by `session_id` is already the complete,
ordered record of every inference/tool/permission decision that
happened, and `request_logs.session_link_id` is what ties a session to
the chat(s) it ran through.
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from sqlalchemy.orm import Session

from ..storage import db as storage_db
from ..storage.db import Agent, Event, LlmAgentSession, RequestLog, Runtime


def chat_ids_for_session(db: Session, link_id: int) -> list[int]:
    rows = (
        db.query(RequestLog.chat_id)
        .filter(RequestLog.session_link_id == link_id, RequestLog.chat_id.isnot(None))
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows})


def session_summary(db: Session, link: LlmAgentSession) -> dict:
    runtime = db.get(Runtime, link.runtime_id)
    agent = db.get(Agent, link.agent_id)
    return {
        "id": link.id,
        "runtime_id": link.runtime_id,
        "runtime_name": runtime.name if runtime else None,
        "agent_id": link.agent_id,
        "agent_name": agent.name if agent else None,
        "started_at": link.started_at,
        "ended_at": link.ended_at,
        "status": link.status,
        "requests_count": link.requests_count,
        "prompt_tokens_total": link.prompt_tokens_total,
        "completion_tokens_total": link.completion_tokens_total,
        "avg_latency_ms": link.avg_latency_ms,
        "avg_tokens_per_sec": link.avg_tokens_per_sec,
        "chat_ids": chat_ids_for_session(db, link.id),
    }


def session_events(db: Session, link_id: int) -> list[dict]:
    """Chronological (oldest first) -- list_events() is newest-first for
    the live log view, but a narrative/export reads naturally in the
    order things actually happened."""
    rows = storage_db.list_events(db, session_id=link_id, limit=100_000)
    out = [
        {
            "id": e.id,
            "event_type": e.event_type,
            "timestamp": e.timestamp,
            "runtime_id": e.runtime_id,
            "agent_id": e.agent_id,
            "metadata": e.metadata_json,
        }
        for e in rows
    ]
    out.reverse()
    return out


def export_json(db: Session, link: LlmAgentSession) -> str:
    return json.dumps(
        {"session": session_summary(db, link), "events": session_events(db, link.id)}, indent=2, ensure_ascii=False
    )


def export_jsonl(db: Session, link: LlmAgentSession) -> str:
    lines = [json.dumps({"type": "session", **session_summary(db, link)}, ensure_ascii=False)]
    lines += [json.dumps({"type": "event", **ev}, ensure_ascii=False) for ev in session_events(db, link.id)]
    return "\n".join(lines) + "\n"


def _fmt_ms(v) -> str:
    return f"{v:.0f} ms" if isinstance(v, (int, float)) else "an unknown duration"


def _fmt_tokens(m: dict) -> str:
    p, c = m.get("prompt_tokens"), m.get("completion_tokens")
    return f"{p if p is not None else '?'} prompt / {c if c is not None else '?'} completion tokens"


_MARKDOWN_RENDERERS: dict[str, Callable[[dict], str]] = {
    "inference.started": lambda m: "Model call started.",
    "inference.completed": lambda m: f"Model call completed — {_fmt_tokens(m)}, {_fmt_ms(m.get('latency_ms'))}.",
    "inference.failed": lambda m: f"Model call failed: {m.get('error', 'unknown error')}",
    "tool.called": lambda m: f"Called tool `{m.get('tool')}` with `{json.dumps(m.get('arguments') or {}, ensure_ascii=False)}`.",
    "tool.completed": lambda m: f"Tool `{m.get('tool')}` completed.",
    "tool.failed": lambda m: f"Tool `{m.get('tool')}` failed ({m.get('reason', 'error')})"
    + (f": {m['error']}" if m.get("error") else "")
    + ".",
    "permission.requested": lambda m: f"Permission requested for `{m.get('scope_key')}` (risk: {m.get('risk_level')}).",
    "permission.approved": lambda m: f"Permission approved: `{m.get('decision')}`.",
    "permission.denied": lambda m: "Permission denied." + (f" ({m['reason']})" if m.get("reason") else ""),
    "session.started": lambda m: "Session started.",
    "session.completed": lambda m: "Session completed.",
    "agent.started": lambda m: "Agent began operating through this runtime.",
    "agent.stopped": lambda m: "Agent stopped operating through this runtime.",
}


def export_markdown(db: Session, link: LlmAgentSession) -> str:
    s = session_summary(db, link)
    events = session_events(db, link.id)
    lines = [
        f"# Session #{s['id']}",
        "",
        f"- Runtime: {s['runtime_name'] or s['runtime_id']}",
        f"- Agent: {s['agent_name'] or s['agent_id']}",
        f"- Started: {s['started_at']}",
        f"- Ended: {s['ended_at'] or '(still active)'}",
        f"- Status: {s['status']}",
        f"- Requests: {s['requests_count']} ({_fmt_tokens({'prompt_tokens': s['prompt_tokens_total'], 'completion_tokens': s['completion_tokens_total']})})",
        "",
        "## Timeline",
        "",
    ]
    if not events:
        lines.append("_No events recorded._")
    for ev in events:
        renderer = _MARKDOWN_RENDERERS.get(ev["event_type"])
        text = renderer(ev["metadata"] or {}) if renderer else f"`{ev['event_type']}`"
        lines.append(f"- **{ev['timestamp']}** — {text}")
    return "\n".join(lines) + "\n"


EXPORT_FORMATS: dict[str, tuple[Callable[[Session, LlmAgentSession], str], str, str]] = {
    "json": (export_json, "application/json", "json"),
    "jsonl": (export_jsonl, "application/x-ndjson", "jsonl"),
    "markdown": (export_markdown, "text/markdown", "md"),
}
