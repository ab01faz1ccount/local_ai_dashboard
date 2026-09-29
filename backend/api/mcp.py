"""
backend/api/mcp.py

REST routes for the MCP Manager (master build prompt Phase 5). Own router,
mounted by main.py like discovery.py:

    GET    /api/v1/mcp/servers
    POST   /api/v1/mcp/servers
    GET    /api/v1/mcp/servers/{id}
    PATCH  /api/v1/mcp/servers/{id}
    DELETE /api/v1/mcp/servers/{id}
    POST   /api/v1/mcp/servers/{id}/connect
    POST   /api/v1/mcp/servers/{id}/disconnect
    GET    /api/v1/mcp/servers/{id}/tools

Every route requires the local access token (router-level dependency).

Secrets: `env` and `headers` values are write-only. Responses only carry
the KEY names (`env_keys`, `header_keys`) -- API keys pasted into a
server's env never travel back out through the API, the events log, or
error messages. To edit: send a partial `env`/`headers` object, where a
key set to `null` deletes it and any other value sets it (keys you leave
out are untouched).

Deliberately absent: any route that CALLS a tool. Running a tool needs
the Permission Engine first (next phases); see core/mcp/manager.py.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core import events
from ..core.mcp import McpBusyError, McpValidationError, mcp_manager
from ..core.mcp import validation as v
from ..storage.db import McpServer
from .http import get_db_session, require_token

router = APIRouter(prefix="/api/v1/mcp", dependencies=[Depends(require_token)])


# ---------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------

class McpServerCreate(BaseModel):
    name: str
    description: Optional[str] = None
    transport: str = "stdio"
    command: Optional[str] = None
    args: list[str] = []
    env: dict[str, str] = {}
    url: Optional[str] = None
    headers: dict[str, str] = {}
    enabled: bool = True


class McpServerUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    transport: Optional[str] = None
    command: Optional[str] = None
    args: Optional[list[str]] = None
    # partial: {"KEY": "value"} sets, {"KEY": null} deletes, absent keys untouched
    env: Optional[dict[str, Optional[str]]] = None
    url: Optional[str] = None
    headers: Optional[dict[str, Optional[str]]] = None
    enabled: Optional[bool] = None


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------

def _to_dict(s: McpServer) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "transport": s.transport,
        "command": s.command,
        "args": list((s.args_json or {}).get("args", [])),
        "env_keys": sorted((s.env_json or {}).keys()),
        "url": s.url,
        "header_keys": sorted((s.headers_json or {}).keys()),
        "is_remote": bool(s.url) and not v.is_loopback_url(s.url),
        "enabled": bool(s.enabled),
        "status": s.status,
        "last_error": s.last_error,
        "tools_count": s.tools_count,
        "server_info": s.server_info_json or {},
        "source_system": s.source_system,
        "project_id": s.project_id,
        "agent_id": s.agent_id,
        "session_id": s.session_id,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def _get_or_404(db: Session, server_id: int) -> McpServer:
    row = db.get(McpServer, server_id)
    if row is None:
        raise HTTPException(404, "سرور MCP پیدا نشد.")
    return row


def _bad(exc: McpValidationError) -> HTTPException:
    return HTTPException(400, str(exc))


def _merge_secret_map(current: dict, patch: dict[str, Optional[str]]) -> dict:
    merged = dict(current or {})
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def _is_active(row: McpServer) -> bool:
    return row.status in ("CONNECTED", "CONNECTING") or mcp_manager.is_connected(row.id)


# ---------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------

@router.get("/servers")
def list_servers(db: Session = Depends(get_db_session)):
    return [_to_dict(s) for s in db.query(McpServer).order_by(McpServer.id).all()]


@router.get("/servers/{server_id}")
def get_server(server_id: int, db: Session = Depends(get_db_session)):
    return _to_dict(_get_or_404(db, server_id))


@router.post("/servers", status_code=201)
def create_server(body: McpServerCreate, db: Session = Depends(get_db_session)):
    try:
        name = v.validate_name(body.name)
        cfg = v.validate_server_config(
            transport=body.transport, command=body.command, args=body.args,
            env=body.env, url=body.url, headers=body.headers,
        )
    except McpValidationError as exc:
        raise _bad(exc)

    row = McpServer(
        name=name,
        description=body.description,
        transport=cfg["transport"],
        command=cfg["command"],
        args_json={"args": cfg["args"]},
        env_json=cfg["env"],
        url=cfg["url"],
        headers_json=cfg["headers"],
        enabled=body.enabled,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"یه سرور با اسم «{name}» از قبل هست.")
    events.emit(
        events.EventType.MCP_SERVER_REGISTERED,
        metadata={"server_id": row.id, "name": row.name, "transport": row.transport},
    )
    return _to_dict(row)


@router.patch("/servers/{server_id}")
def update_server(server_id: int, body: McpServerUpdate, db: Session = Depends(get_db_session)):
    row = _get_or_404(db, server_id)
    fields = body.model_dump(exclude_unset=True)

    connection_fields = {"transport", "command", "args", "env", "url", "headers"}
    if connection_fields & fields.keys() and _is_active(row):
        raise HTTPException(400, f"«{row.name}» متصله — اول قطعش کن، بعد تنظیمات اتصال رو عوض کن.")

    try:
        if "name" in fields:
            row.name = v.validate_name(fields["name"])
        if "description" in fields:
            row.description = fields["description"]

        if connection_fields & fields.keys():
            transport = fields.get("transport", row.transport)
            env_now = _merge_secret_map(row.env_json, fields["env"]) if "env" in fields else row.env_json
            hdr_now = _merge_secret_map(row.headers_json, fields["headers"]) if "headers" in fields else row.headers_json
            cfg = v.validate_server_config(
                transport=transport,
                command=fields.get("command", row.command),
                args=fields.get("args", (row.args_json or {}).get("args", [])),
                env=env_now,
                url=fields.get("url", row.url),
                headers=hdr_now,
            )
            row.transport = cfg["transport"]
            row.command = cfg["command"]
            row.args_json = {"args": cfg["args"]}
            row.env_json = cfg["env"]
            row.url = cfg["url"]
            row.headers_json = cfg["headers"]
    except McpValidationError as exc:
        db.rollback()
        raise _bad(exc)

    if "enabled" in fields:
        row.enabled = bool(fields["enabled"])

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "یه سرور دیگه با این اسم هست.")

    # Disabling a live server takes it offline -- "enabled" would otherwise
    # be a label that says one thing while the process keeps running.
    if "enabled" in fields and not row.enabled and _is_active(row):
        try:
            mcp_manager.disconnect(db, row)
        except McpBusyError as exc:
            raise HTTPException(409, str(exc))
    return _to_dict(row)


@router.delete("/servers/{server_id}")
def delete_server(server_id: int, db: Session = Depends(get_db_session)):
    row = _get_or_404(db, server_id)
    try:
        mcp_manager.disconnect(db, row)  # stops the child process first; idempotent
    except McpBusyError as exc:
        raise HTTPException(409, str(exc))
    events.emit(events.EventType.MCP_SERVER_REMOVED, metadata={"server_id": row.id, "name": row.name})
    db.delete(row)
    db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------

@router.post("/servers/{server_id}/connect")
def connect_server(server_id: int, db: Session = Depends(get_db_session)):
    row = _get_or_404(db, server_id)
    if not row.enabled:
        raise HTTPException(400, f"«{row.name}» غیرفعاله — اول فعالش کن.")
    try:
        status = mcp_manager.connect(db, row)
    except McpBusyError as exc:
        raise HTTPException(409, str(exc))
    return {**status.to_dict(), "server": _to_dict(row)}


@router.post("/servers/{server_id}/disconnect")
def disconnect_server(server_id: int, db: Session = Depends(get_db_session)):
    row = _get_or_404(db, server_id)
    try:
        status = mcp_manager.disconnect(db, row)
    except McpBusyError as exc:
        raise HTTPException(409, str(exc))
    return {**status.to_dict(), "server": _to_dict(row)}


@router.get("/servers/{server_id}/tools")
def list_server_tools(server_id: int, db: Session = Depends(get_db_session)):
    row = _get_or_404(db, server_id)
    try:
        tools = mcp_manager.list_tools(row.id)
    except Exception as exc:  # the live link broke between connect and now
        raise HTTPException(502, f"گرفتن لیست ابزارها ناموفق بود: {type(exc).__name__}")
    if tools is None:
        raise HTTPException(409, f"«{row.name}» متصل نیست — اول connect کن.")
    if row.tools_count != len(tools):
        row.tools_count = len(tools)
        db.commit()
    return {"server_id": row.id, "tools": tools}
