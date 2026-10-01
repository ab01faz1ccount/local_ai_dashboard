"""
backend/core/events/taxonomy.py

The event taxonomy from the master build prompt (section 16), plus a
small number of documented extensions this app already has real actions
for. Nothing outside this file should spell out an event-type string --
import EventType and use the constant, so a typo can't silently create a
new, unqueryable event type.

Core taxonomy (from the spec, one-to-one):
    runtime.started / runtime.stopped / runtime.crashed
    model.loaded / model.unloaded
    agent.started / agent.stopped
    session.started / session.completed
    inference.started / inference.completed / inference.failed
    tool.called / tool.completed / tool.failed
    mcp.connected / mcp.disconnected
    permission.requested / permission.approved / permission.denied
    runtime.metrics

Extensions (not in the spec's list, added because this app already has
the underlying action and an audit trail is more useful with them than
without): the *.registered/*.removed/*.created/*.deleted catalog-CRUD
events, and model.verification_completed. Each is commented at its
definition so it's obvious it's an addition, not a spec citation.

tool.* is emitted by core/agent_loop.py, mcp.* by core/mcp/manager.py,
and permission.* by core/permissions/engine.py. Declaring an event type
here means future work only has to call `events.emit(...)`, not also
touch this taxonomy.
"""

from __future__ import annotations

from enum import Enum


class EventType(str, Enum):
    # -- runtime lifecycle (process start/stop, not DB row CRUD) --
    RUNTIME_STARTED = "runtime.started"
    RUNTIME_STOPPED = "runtime.stopped"
    RUNTIME_CRASHED = "runtime.crashed"
    RUNTIME_METRICS = "runtime.metrics"

    # -- runtime catalog CRUD (extension: not in the spec's list) --
    RUNTIME_REGISTERED = "runtime.registered"
    RUNTIME_REMOVED = "runtime.removed"

    # -- model lifecycle --
    MODEL_LOADED = "model.loaded"
    MODEL_UNLOADED = "model.unloaded"

    # -- model catalog CRUD (extension) --
    MODEL_REGISTERED = "model.registered"
    MODEL_REMOVED = "model.removed"
    MODEL_VERIFICATION_COMPLETED = "model.verification_completed"  # extension

    # -- agent lifecycle --
    AGENT_STARTED = "agent.started"
    AGENT_STOPPED = "agent.stopped"

    # -- agent catalog CRUD (extension) --
    AGENT_CREATED = "agent.created"
    AGENT_DELETED = "agent.deleted"

    # -- session --
    SESSION_STARTED = "session.started"
    SESSION_COMPLETED = "session.completed"

    # -- inference --
    INFERENCE_STARTED = "inference.started"
    INFERENCE_COMPLETED = "inference.completed"
    INFERENCE_FAILED = "inference.failed"

    # -- Tool Registry / agent loop (core/agent_loop.py) --
    TOOL_CALLED = "tool.called"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"

    # -- MCP Manager (core/mcp/manager.py) --
    MCP_CONNECTED = "mcp.connected"
    MCP_DISCONNECTED = "mcp.disconnected"
    # extensions: a connect attempt that never got as far as "connected"
    # is NOT an mcp.disconnected (nothing was connected), and the catalog
    # CRUD events mirror runtime/model/agent's.
    MCP_CONNECT_FAILED = "mcp.connect_failed"
    MCP_SERVER_REGISTERED = "mcp.server_registered"
    MCP_SERVER_REMOVED = "mcp.server_removed"

    # -- Permission Engine (core/permissions/engine.py) --
    PERMISSION_REQUESTED = "permission.requested"
    PERMISSION_APPROVED = "permission.approved"
    PERMISSION_DENIED = "permission.denied"


# One short, human-readable line per type -- backs GET /api/v1/events/types,
# which the frontend uses to populate a filter dropdown without hardcoding
# the list in two places.
EVENT_TYPE_DESCRIPTIONS: dict[EventType, str] = {
    EventType.RUNTIME_STARTED: "A runtime's process came online.",
    EventType.RUNTIME_STOPPED: "A runtime's process stopped (requested or detected).",
    EventType.RUNTIME_CRASHED: "A runtime's process ended up in an error state.",
    EventType.RUNTIME_METRICS: "A periodic runtime performance sample.",
    EventType.RUNTIME_REGISTERED: "A runtime configuration was added.",
    EventType.RUNTIME_REMOVED: "A runtime configuration was deleted.",
    EventType.MODEL_LOADED: "A model was loaded by a runtime that started.",
    EventType.MODEL_UNLOADED: "A model was unloaded because its runtime stopped.",
    EventType.MODEL_REGISTERED: "A model file was added to the catalog.",
    EventType.MODEL_REMOVED: "A model was removed from the catalog.",
    EventType.MODEL_VERIFICATION_COMPLETED: "A model's authenticity check finished.",
    EventType.AGENT_STARTED: "An agent began using a runtime.",
    EventType.AGENT_STOPPED: "An agent stopped using a runtime.",
    EventType.AGENT_CREATED: "An agent was created.",
    EventType.AGENT_DELETED: "An agent was deleted.",
    EventType.SESSION_STARTED: "A runtime<->agent session began.",
    EventType.SESSION_COMPLETED: "A runtime<->agent session ended.",
    EventType.INFERENCE_STARTED: "A chat completion request was sent to a runtime.",
    EventType.INFERENCE_COMPLETED: "A chat completion request finished successfully.",
    EventType.INFERENCE_FAILED: "A chat completion request failed.",
    EventType.TOOL_CALLED: "An agent called a tool.",
    EventType.TOOL_COMPLETED: "A tool call finished successfully.",
    EventType.TOOL_FAILED: "A tool call failed, was denied, or hit an unknown/disconnected tool.",
    EventType.MCP_CONNECTED: "An MCP server connected.",
    EventType.MCP_DISCONNECTED: "An MCP server disconnected (requested, or the connection was lost).",
    EventType.MCP_CONNECT_FAILED: "A connection attempt to an MCP server failed.",
    EventType.MCP_SERVER_REGISTERED: "An MCP server configuration was added.",
    EventType.MCP_SERVER_REMOVED: "An MCP server configuration was deleted.",
    EventType.PERMISSION_REQUESTED: "A permission was requested and is waiting on a decision.",
    EventType.PERMISSION_APPROVED: "A permission request was approved (once, for the session, or always).",
    EventType.PERMISSION_DENIED: "A permission request was denied, or timed out unanswered.",
}


def describe_all() -> list[dict]:
    """[{"event_type": "runtime.started", "description": "..."}], sorted
    alphabetically -- backs GET /api/v1/events/types."""
    return [
        {"event_type": t.value, "description": EVENT_TYPE_DESCRIPTIONS[t]}
        for t in sorted(EventType, key=lambda t: t.value)
    ]
