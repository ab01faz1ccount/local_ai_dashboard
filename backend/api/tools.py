"""
backend/api/tools.py

Read/enable-toggle routes for the Tool Registry (storage/db.py: Tool,
kept in sync by core/tools/registry.py). Own router, mounted by main.py:

    GET   /api/v1/tools                -- every cached tool, newest server first then name
    GET   /api/v1/tools/{id}
    PATCH /api/v1/tools/{id}            -- the only user-settable field: enabled

There is no POST or DELETE here: a Tool row is derived from what an MCP
server reports (api/mcp.py syncs it on connect/refresh), never created
or removed by hand -- disabling a tool the user doesn't want offered to
the model is a PATCH, not a delete, so it comes back correctly the next
time the server's tool list is synced.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.mcp import mcp_manager
from ..storage.db import McpServer, Tool
from .http import get_db_session, require_token

router = APIRouter(prefix="/api/v1/tools", dependencies=[Depends(require_token)])


class ToolUpdate(BaseModel):
    enabled: bool


def _to_dict(t: Tool, server: Optional[McpServer] = None) -> dict:
    return {
        "id": t.id,
        "mcp_server_id": t.mcp_server_id,
        "server_name": server.name if server is not None else None,
        "server_status": server.status if server is not None else None,
        "name": t.name,
        "description": t.description,
        "input_schema": t.input_schema_json or {},
        "annotations": t.annotations_json or {},
        "risk_level": t.risk_level,
        "enabled": t.enabled,
        "connected": mcp_manager.is_connected(t.mcp_server_id),
        "updated_at": t.updated_at,
    }


def _get_or_404(db: Session, tool_id: int) -> Tool:
    row = db.get(Tool, tool_id)
    if row is None:
        raise HTTPException(404, "این ابزار پیدا نشد.")
    return row


@router.get("")
def list_tools(
    mcp_server_id: Optional[int] = None,
    enabled_only: bool = False,
    db: Session = Depends(get_db_session),
):
    q = db.query(Tool)
    if mcp_server_id is not None:
        q = q.filter(Tool.mcp_server_id == mcp_server_id)
    if enabled_only:
        q = q.filter(Tool.enabled.is_(True))
    rows = q.order_by(Tool.mcp_server_id, Tool.name).all()
    servers = {s.id: s for s in db.query(McpServer).all()}
    return [_to_dict(t, servers.get(t.mcp_server_id)) for t in rows]


@router.get("/{tool_id}")
def get_tool(tool_id: int, db: Session = Depends(get_db_session)):
    t = _get_or_404(db, tool_id)
    return _to_dict(t, db.get(McpServer, t.mcp_server_id))


@router.patch("/{tool_id}")
def update_tool(tool_id: int, body: ToolUpdate, db: Session = Depends(get_db_session)):
    t = _get_or_404(db, tool_id)
    t.enabled = body.enabled
    db.commit()
    return _to_dict(t, db.get(McpServer, t.mcp_server_id))
