"""
backend/core/tools/registry.py

The Tool Registry (master build prompt, part of Phase 4): keeps the
`tools` table (storage/db.py) in sync with what each connected MCP
server actually lists, so core/agent_loop.py -- and any settings page --
can read "what's callable" from the database instead of reaching into
core/mcp/manager.py's live sessions on every turn.

Not a class: `sync_server_tools` is the only real operation (upsert this
server's tools from a fresh list, drop what's no longer there), so a
plain function keeps this a thin layer over the DB rather than another
piece of long-lived state. Risk level is recomputed on every sync from
the tool's own annotations (core.permissions.classify_mcp_tool_risk) --
a server can change what it reports between connects, and `enabled` is
the only field a user sets here, so nothing about re-deriving risk on
sync can silently undo their choice.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..permissions import classify_mcp_tool_risk
from ...storage.db import Tool


def sync_server_tools(db: Session, mcp_server_id: int, tools: list[dict]) -> list[Tool]:
    """`tools`: the dicts core.mcp.manager.McpManager.list_tools /
    cached_tools returns (each has at least name/description/
    input_schema, optionally annotations). Returns the server's current
    Tool rows after syncing, ordered by name."""
    existing = {t.name: t for t in db.query(Tool).filter(Tool.mcp_server_id == mcp_server_id).all()}
    seen: set[str] = set()

    for tool in tools:
        name = tool["name"]
        seen.add(name)
        row = existing.get(name)
        if row is None:
            row = Tool(mcp_server_id=mcp_server_id, name=name)
            db.add(row)
        row.description = tool.get("description") or ""
        row.input_schema_json = tool.get("input_schema") or {}
        row.annotations_json = tool.get("annotations") or {}
        row.risk_level = classify_mcp_tool_risk(tool)

    for name, row in existing.items():
        if name not in seen:
            db.delete(row)

    db.commit()
    return db.query(Tool).filter(Tool.mcp_server_id == mcp_server_id).order_by(Tool.name).all()
