"""
backend/core/permissions/engine.py

Permission Engine (master build prompt section 20). The layer that sits
in front of every risky action a tool could take: a caller (today:
nothing yet -- the future Tool Registry / agent loop) asks
`check_or_request(...)`, and either gets an immediate answer from a
standing grant, or the call blocks while a human decides, live, via
GET /api/v1/permissions/pending + POST /api/v1/permissions/requests/{id}/resolve.

Same shape as core/mcp/manager.py on purpose: a synchronous method the
caller can just call, backed by a small piece of concurrency machinery
(there: an asyncio loop thread + a Future; here: an in-memory dict of
pending requests + threading.Event, since there's no long-lived
connection to supervise -- just one wait). `resolve()` never touches the
database; only the blocked caller's own thread persists the decision
once it wakes, so two different requests' DB sessions are never shared
across threads.

Decisions and how long they last (`PermissionGrant.expires_at` in
storage/db.py):
  - allow_once / deny  -> settle only the one pending request. Recorded
    for history with expires_at = granted_at (already expired), so
    neither can silently auto-apply to a later request.
  - allow_session       -> reusable for the rest of THIS session_id
    (expires_at stays NULL; scoping is by session_id match, not time).
  - allow_always        -> reusable forever for this scope (expires_at
    NULL, no session restriction).

Nothing here is MCP-specific -- `scope_type`/`scope_key` are opaque
strings, and `mcp_server_scope`/`mcp_tool_scope` below are just the two
scope kinds core/mcp/manager.py's tool listings will want. A future tool
kind mints its own scope_type without touching this file.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from .. import events
from ...storage.db import PermissionGrant, utcnow_iso

RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
DECISIONS = ("allow_once", "allow_session", "allow_always", "deny")
SCOPE_TYPES = ("mcp_server", "mcp_tool")

# A request nobody answers must eventually give up rather than hang a
# caller (and, transitively, whatever the caller was doing) forever.
DEFAULT_TIMEOUT = 120.0

_RANK = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def mcp_server_scope(server_id: int) -> tuple[str, str]:
    return "mcp_server", str(server_id)


def mcp_tool_scope(server_id: int, tool_name: str) -> tuple[str, str]:
    return "mcp_tool", f"{server_id}:{tool_name}"


def classify_mcp_tool_risk(tool: dict) -> str:
    """A starting-point risk level for an MCP tool, from the
    `annotations` an MCP server may attach to it (readOnlyHint /
    destructiveHint / openWorldHint -- see the MCP spec's
    ToolAnnotations). These hints are server-declared, not verified --
    a careless or malicious server can attach a flattering annotation to
    a dangerous tool -- so this is only ever a default the approval UI
    can pre-select; it is never used to skip asking.

    destructiveHint=true wins outright (CRITICAL). Otherwise
    readOnlyHint=true is LOW, an explicit destructiveHint=false (without
    readOnlyHint) is MEDIUM, and no hints at all is HIGH -- undeclared
    write potential is treated cautiously, not optimistically.
    openWorldHint=true (the tool reaches outside this machine) then
    bumps the result up one level, since that's an extra way for a
    request to do something a human didn't expect.
    """
    ann = tool.get("annotations") or {}
    destructive = ann.get("destructiveHint")
    read_only = ann.get("readOnlyHint")
    open_world = ann.get("openWorldHint")

    if destructive is True:
        level = "CRITICAL"
    elif read_only is True:
        level = "LOW"
    elif destructive is False:
        level = "MEDIUM"
    else:
        level = "HIGH"

    if open_world is True:
        level = _RANK[min(_RANK.index(level) + 1, len(_RANK) - 1)]
    return level


@dataclass
class PendingRequest:
    id: str
    scope_type: str
    scope_key: str
    risk_level: str
    session_id: Optional[int]
    agent_id: Optional[int]
    description: Optional[str]
    requested_at: str
    event: threading.Event = field(default_factory=threading.Event)
    decision: Optional[str] = None  # set by resolve(), read by the waiter after event fires

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "scope_type": self.scope_type,
            "scope_key": self.scope_key,
            "risk_level": self.risk_level,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "description": self.description,
            "requested_at": self.requested_at,
        }


class PermissionEngine:
    def __init__(self, *, default_timeout: float = DEFAULT_TIMEOUT) -> None:
        self.default_timeout = default_timeout
        self._pending: dict[str, PendingRequest] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # standing grants
    # ------------------------------------------------------------------

    def _find_existing_grant(
        self, db: Session, scope_type: str, scope_key: str, session_id: Optional[int]
    ) -> Optional[PermissionGrant]:
        rows = (
            db.query(PermissionGrant)
            .filter(
                PermissionGrant.scope_type == scope_type,
                PermissionGrant.scope_key == scope_key,
                PermissionGrant.expires_at.is_(None),
                PermissionGrant.decision.in_(("allow_always", "allow_session")),
            )
            .order_by(PermissionGrant.id.desc())
            .all()
        )
        for g in rows:
            if g.decision == "allow_always":
                return g
            if g.decision == "allow_session" and session_id is not None and g.session_id == session_id:
                return g
        return None

    def _persist_decision(self, db: Session, req: PendingRequest, decision: str) -> PermissionGrant:
        now = utcnow_iso()
        grant = PermissionGrant(
            scope_type=req.scope_type,
            scope_key=req.scope_key,
            risk_level=req.risk_level,
            decision=decision,
            description=req.description,
            agent_id=req.agent_id,
            session_id=req.session_id,
            granted_at=now,
            # allow_once / deny settle only this one request -- expired on arrival.
            expires_at=now if decision in ("allow_once", "deny") else None,
        )
        db.add(grant)
        db.commit()
        return grant

    def revoke(self, db: Session, grant_id: int) -> Optional[PermissionGrant]:
        """Ends a standing grant early. A no-op (returns the row as-is)
        on a grant that was never standing or is already ended."""
        grant = db.get(PermissionGrant, grant_id)
        if grant is None:
            return None
        if grant.expires_at is None:
            grant.expires_at = utcnow_iso()
            db.commit()
        return grant

    # ------------------------------------------------------------------
    # the synchronous check/request call
    # ------------------------------------------------------------------

    def check_or_request(
        self,
        db: Session,
        *,
        scope_type: str,
        scope_key: str,
        risk_level: str,
        session_id: Optional[int] = None,
        agent_id: Optional[int] = None,
        description: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> dict:
        if scope_type not in SCOPE_TYPES:
            raise ValueError(f"unknown scope_type {scope_type!r}")
        if risk_level not in RISK_LEVELS:
            raise ValueError(f"unknown risk_level {risk_level!r}")

        existing = self._find_existing_grant(db, scope_type, scope_key, session_id)
        if existing is not None:
            return {"decision": "allow", "source": "existing_grant", "grant_id": existing.id, "request_id": None}

        # Dedup: an identical in-flight request (same scope + session)
        # gets a second waiter on the SAME event/result rather than a
        # second live prompt -- threading.Event supports any number of
        # waiters, so this is just "don't create a new PendingRequest".
        with self._lock:
            req = next(
                (
                    r
                    for r in self._pending.values()
                    if r.scope_type == scope_type and r.scope_key == scope_key and r.session_id == session_id
                ),
                None,
            )
            is_new = req is None
            if is_new:
                req = PendingRequest(
                    id=uuid.uuid4().hex,
                    scope_type=scope_type,
                    scope_key=scope_key,
                    risk_level=risk_level,
                    session_id=session_id,
                    agent_id=agent_id,
                    description=description,
                    requested_at=utcnow_iso(),
                )
                self._pending[req.id] = req

        if is_new:
            events.emit(
                events.EventType.PERMISSION_REQUESTED,
                agent_id=agent_id,
                session_id=session_id,
                metadata={
                    "request_id": req.id,
                    "scope_type": scope_type,
                    "scope_key": scope_key,
                    "risk_level": risk_level,
                    "description": description,
                },
            )

        got = req.event.wait(timeout if timeout is not None else self.default_timeout)

        if is_new:
            with self._lock:
                self._pending.pop(req.id, None)

        if not got:
            if is_new:
                # A timeout is transient, not a standing decision -- nothing is
                # persisted, so the very next check asks again rather than
                # silently treating "nobody answered" as "always deny".
                events.emit(
                    events.EventType.PERMISSION_DENIED,
                    agent_id=agent_id,
                    session_id=session_id,
                    metadata={"request_id": req.id, "scope_type": scope_type, "scope_key": scope_key, "reason": "timeout"},
                )
            return {"decision": "deny", "source": "timeout", "grant_id": None, "request_id": req.id}

        decision = req.decision
        if is_new:
            grant = self._persist_decision(db, req, decision)
            event_type = events.EventType.PERMISSION_DENIED if decision == "deny" else events.EventType.PERMISSION_APPROVED
            events.emit(
                event_type,
                agent_id=agent_id,
                session_id=session_id,
                metadata={
                    "request_id": req.id,
                    "scope_type": scope_type,
                    "scope_key": scope_key,
                    "decision": decision,
                    "grant_id": grant.id,
                },
            )
            grant_id = grant.id
        else:
            # A dedup'd waiter didn't create the grant -- look up what the
            # original requester's persist just committed, by scope + the
            # exact moment this request resolved (best-effort; if it can't
            # find one -- a race with a revoke, say -- it still returns the
            # correct decision, just without a grant_id to point at).
            existing_now = self._find_existing_grant(db, scope_type, scope_key, session_id)
            grant_id = existing_now.id if existing_now else None

        return {
            "decision": "deny" if decision == "deny" else "allow",
            "source": "live_decision",
            "grant_id": grant_id,
            "request_id": req.id,
            "decision_kind": decision,
        }

    # ------------------------------------------------------------------
    # resolving a pending request (called from a different thread/request)
    # ------------------------------------------------------------------

    def resolve(self, request_id: str, decision: str) -> bool:
        if decision not in DECISIONS:
            raise ValueError(f"unknown decision {decision!r}")
        with self._lock:
            req = self._pending.get(request_id)
        if req is None:
            return False
        req.decision = decision
        req.event.set()
        return True

    def list_pending(self) -> list[PendingRequest]:
        with self._lock:
            return list(self._pending.values())

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)


# One process, one engine -- same reasoning as runtime_manager/mcp_manager.
permission_engine = PermissionEngine()
