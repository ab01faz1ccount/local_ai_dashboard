"""
backend/api/permissions.py

REST routes for the Permission Engine (master build prompt section 20).
Own router, mounted by main.py like discovery.py / mcp.py:

    POST   /api/v1/permissions/check                     -- blocks until decided or timed out
    GET    /api/v1/permissions/pending                    -- what's currently waiting on a human
    POST   /api/v1/permissions/requests/{id}/resolve      -- answer a pending request
    GET    /api/v1/permissions/grants                     -- standing/historical decisions
    DELETE /api/v1/permissions/grants/{id}                -- revoke a standing grant early

Every route requires the local access token. `check` is intentionally a
plain `def` (not `async def`): FastAPI runs sync routes in a thread-pool
worker, so a call that blocks for up to `timeout_seconds` waiting on a
human decision does not stall the event loop -- a concurrent `resolve`
(or any other request) is served on another worker thread while it waits.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.permissions import SCOPE_TYPES, permission_engine
from ..storage.db import PermissionGrant
from .http import get_db_session, require_token

router = APIRouter(prefix="/api/v1/permissions", dependencies=[Depends(require_token)])

MAX_TIMEOUT = 600.0  # generous ceiling so one bad request can't tie up a worker thread indefinitely


class CheckRequest(BaseModel):
    scope_type: Literal["mcp_server", "mcp_tool"]
    scope_key: str
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    session_id: Optional[int] = None
    agent_id: Optional[int] = None
    description: Optional[str] = None
    timeout_seconds: float = Field(default=120.0, gt=0, le=MAX_TIMEOUT)


class ResolveRequest(BaseModel):
    decision: Literal["allow_once", "allow_session", "allow_always", "deny"]


def _grant_to_dict(g: PermissionGrant) -> dict:
    return {
        "id": g.id,
        "scope_type": g.scope_type,
        "scope_key": g.scope_key,
        "risk_level": g.risk_level,
        "decision": g.decision,
        "description": g.description,
        "agent_id": g.agent_id,
        "session_id": g.session_id,
        "granted_at": g.granted_at,
        "expires_at": g.expires_at,
        "active": g.expires_at is None,
    }


@router.post("/check")
def check_permission(body: CheckRequest, db: Session = Depends(get_db_session)):
    try:
        result = permission_engine.check_or_request(
            db,
            scope_type=body.scope_type,
            scope_key=body.scope_key,
            risk_level=body.risk_level,
            session_id=body.session_id,
            agent_id=body.agent_id,
            description=body.description,
            timeout=body.timeout_seconds,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "session_id یا agent_id نامعتبره.")
    return result


@router.get("/pending")
def list_pending():
    return [r.to_dict() for r in permission_engine.list_pending()]


@router.post("/requests/{request_id}/resolve")
def resolve_request(request_id: str, body: ResolveRequest):
    ok = permission_engine.resolve(request_id, body.decision)
    if not ok:
        raise HTTPException(404, "این درخواست دیگه در انتظار نیست (شاید قبلاً پاسخ داده شده یا timeout شده).")
    return {"resolved": True}


@router.get("/grants")
def list_grants(
    scope_type: Optional[str] = None,
    scope_key: Optional[str] = None,
    active_only: bool = False,
    db: Session = Depends(get_db_session),
):
    if scope_type is not None and scope_type not in SCOPE_TYPES:
        raise HTTPException(400, f"scope_type باید یکی از {', '.join(SCOPE_TYPES)} باشه.")
    q = db.query(PermissionGrant)
    if scope_type is not None:
        q = q.filter(PermissionGrant.scope_type == scope_type)
    if scope_key is not None:
        q = q.filter(PermissionGrant.scope_key == scope_key)
    if active_only:
        q = q.filter(PermissionGrant.expires_at.is_(None))
    rows = q.order_by(PermissionGrant.id.desc()).all()
    return [_grant_to_dict(g) for g in rows]


@router.delete("/grants/{grant_id}")
def revoke_grant(grant_id: int, db: Session = Depends(get_db_session)):
    grant = permission_engine.revoke(db, grant_id)
    if grant is None:
        raise HTTPException(404, "این grant پیدا نشد.")
    return _grant_to_dict(grant)
