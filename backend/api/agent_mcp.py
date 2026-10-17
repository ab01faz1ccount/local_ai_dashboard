"""
backend/api/agent_mcp.py

Hand the MCP servers attached to an agent to that agent's OWN config, so an
external agent (Hermes, Open Interpreter, ...) can use them too.

    GET  /api/v1/agents/{id}/mcp          -- what's in the agent's file vs. what's attached (read-only)
    POST /api/v1/agents/{id}/mcp/sync     -- write `lai_*` entries, read them back, report
    POST /api/v1/agents/{id}/mcp/probe    -- ask the agent's own CLI about one server

See core/agents/mcp_sync.py for what is and isn't touched. The app's
Permission Engine does not govern an external agent's tool calls; every
response says so in `permission_note`, so the UI can't pretend otherwise.
"""

from __future__ import annotations

import subprocess
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core import agents as agent_backends
from ..core.agents import mcp_sync
from ..storage.db import Agent, McpServer, Tool
from .http import get_db_session, require_token

router = APIRouter(prefix="/api/v1/agents", dependencies=[Depends(require_token)])

PROBE_TIMEOUT_SECONDS = 45
PROBE_OUTPUT_CHARS = 1500

PERMISSION_NOTE = (
    "Once written, this agent runs these tools itself. The app's permission prompts do not apply to it; "
    "tools you disabled in the app are excluded only if the agent supports that."
)


class SyncRequest(BaseModel):
    confirm_secrets: bool = False


class ProbeRequest(BaseModel):
    server: str


def _adapter_and_store(db: Session, agent_id: int):
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")
    if agent.agent_backend == "generic":
        raise HTTPException(400, "This agent has no real backend, so there is no config to write to.")
    try:
        adapter = agent_backends.resolve_adapter(agent.agent_backend, agent.config_json)
    except agent_backends.SpecError as exc:
        raise HTTPException(400, f"cli_spec: {exc}")
    if adapter is None:
        raise HTTPException(400, f"backend '{agent.agent_backend}' is not known.")
    store = adapter.mcp_store()
    if store is None:
        raise HTTPException(400, f"{adapter.display_name}: no MCP config file is known for this agent.")
    return agent, adapter, store


def _entries(db: Session, agent: Agent) -> list[mcp_sync.McpEntry]:
    ids = agent.config_json.get("mcp_server_ids") or []
    out: list[mcp_sync.McpEntry] = []
    for sid in ids:
        s = db.get(McpServer, sid)
        if s is None or not s.enabled:
            continue
        disabled = [t.name for t in db.query(Tool).filter(Tool.mcp_server_id == s.id, Tool.enabled.is_(False)).all()]
        out.append(
            mcp_sync.McpEntry(
                name=s.name,
                transport=s.transport,
                command=s.command,
                args=list((s.args_json or {}).get("args") or []),
                env=dict(s.env_json or {}),
                url=s.url,
                headers=dict(s.headers_json or {}),
                disabled_tools=disabled,
            )
        )
    return out


def _payload(adapter, report: mcp_sync.SyncReport) -> dict:
    d = report.to_dict()
    d["agent_name"] = adapter.display_name
    d["permission_note"] = PERMISSION_NOTE
    d["in_sync"] = report.in_sync
    d["can_probe"] = adapter.mcp_probe_command("lai_probe") is not None
    d["probe_is_live"] = adapter.mcp_probe_is_live()
    return d


@router.get("/{agent_id}/mcp")
def get_agent_mcp(agent_id: int, db: Session = Depends(get_db_session)):
    agent, adapter, store = _adapter_and_store(db, agent_id)
    try:
        report = mcp_sync.status(_entries(db, agent), store)
    except mcp_sync.McpSyncError as exc:
        raise HTTPException(400, str(exc))
    return _payload(adapter, report)


@router.post("/{agent_id}/mcp/sync")
def sync_agent_mcp(agent_id: int, body: SyncRequest, db: Session = Depends(get_db_session)):
    agent, adapter, store = _adapter_and_store(db, agent_id)
    try:
        report = mcp_sync.sync(_entries(db, agent), store, confirm_secrets=body.confirm_secrets)
    except mcp_sync.McpSyncError as exc:
        raise HTTPException(400, str(exc))
    return _payload(adapter, report)


@router.post("/{agent_id}/mcp/probe")
def probe_agent_mcp(agent_id: int, body: ProbeRequest, db: Session = Depends(get_db_session)):
    agent, adapter, store = _adapter_and_store(db, agent_id)
    entry = next((e for e in _entries(db, agent) if e.name == body.server), None)
    if entry is None:
        raise HTTPException(404, "that server is not attached to this agent")
    cmd = adapter.mcp_probe_command(entry.key)
    if not cmd:
        raise HTTPException(400, f"{adapter.display_name} has no CLI command to check an MCP server.")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS)
        text = ((r.stdout or "") + ("\n" + r.stderr if r.stderr else "")).strip()
        ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        text, ok = f"No answer within {PROBE_TIMEOUT_SECONDS} s.", False
    except OSError as exc:
        text, ok = f"Could not run the agent's CLI: {exc}", False
    text = mcp_sync.redact(text, mcp_sync.entry_secrets(entry))
    return {
        "server": entry.name,
        "agent_key": entry.key,
        "command": " ".join(cmd),
        "ok": ok,
        "live": adapter.mcp_probe_is_live(),
        "output": text[-PROBE_OUTPUT_CHARS:],
    }
