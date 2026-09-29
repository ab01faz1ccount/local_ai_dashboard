"""
backend/storage/db.py

SQLAlchemy ORM layer for the Local AI Control Center.

This file is the single source of truth for the data layer: it defines the
ORM models (mirroring schema.sql 1:1), the engine/session setup, and a set
of query helpers for the two things the rest of the app needs most often:

  1. CRUD on runtimes / models / agents
  2. The LLM <-> Agent analytics questions:
       - "which agents are using this LLM right now?"
       - "which LLM(s) is this agent currently attached to?"
       - "what's the historical usage between LLM X and Agent Y?"

Design notes (mirrors schema.sql's comments):
  - JSON-ish config is stored as TEXT and (de)serialized at the ORM boundary
    via a small `JSONText` TypeDecorator, so new llama.cpp params never need
    a migration.
  - `source_system` / `project_id` / `agent_id` / `session_id` columns exist
    on every top-level entity for the future Synapse contract, defaulting to
    local-only values. Nothing else in this file assumes Synapse exists.
  - SQLite is opened with `check_same_thread=False` + a single shared engine,
    which is fine for a single-user local control plane driven by asyncio;
    if this ever needs real concurrent writers, swap the engine, not the
    models.
"""

from __future__ import annotations

import json
import enum
import uuid
from datetime import datetime, timezone
from typing import Optional, Iterable

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
    Session,
)
from sqlalchemy.types import TypeDecorator


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    """ISO-8601 UTC timestamp string, matching schema.sql's STRFTIME format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def utcnow_iso() -> str:
    """Public wrapper around `_utcnow_iso`, for callers outside this
    module (e.g. `api/http.py` stamping `verification_checked_at`)."""
    return _utcnow_iso()


class JSONText(TypeDecorator):
    """Stores a dict as TEXT (JSON), returns a dict on read.

    Lets `config_json` columns behave like real JSON in Python while staying
    a plain TEXT column in SQLite (matches schema.sql exactly, no JSON1
    extension dependency).
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return "{}"
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if not value:
            return {}
        return json.loads(value)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Status enums (kept as plain str constants, not native Python Enum, so the
# CHECK constraints in schema.sql and Python stay trivially in sync)
# ---------------------------------------------------------------------------

class RuntimeStatus(str, enum.Enum):
    OFFLINE = "OFFLINE"
    STARTING = "STARTING"
    ONLINE = "ONLINE"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


class AgentStatus(str, enum.Enum):
    INACTIVE = "INACTIVE"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"


class SessionStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------

class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso, onupdate=_utcnow_iso)


class MLModel(Base):
    """Named MLModel (not Model) to avoid clashing with SQLAlchemy's own
    naming conventions / Base.metadata confusion in imports."""

    __tablename__ = "models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    format: Mapped[str] = mapped_column(String, default="gguf")

    quantization: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    param_count: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    context_length: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    source_system: Mapped[str] = mapped_column(String, default="local")
    project_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # -- Model comparison / authenticity verification (opt-in) --
    # hf_repo_id/hf_filename are set by the user (not auto-detected from a
    # .gguf filename, which isn't reliable enough to trust) and are what
    # both features use to reach out to Hugging Face's API.
    hf_repo_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    hf_filename: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    verification_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    verification_checked_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    verification_details_json: Mapped[dict] = mapped_column(JSONText, default=dict)

    added_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso, onupdate=_utcnow_iso)

    runtimes: Mapped[list["Runtime"]] = relationship(back_populates="model")

    __table_args__ = (
        CheckConstraint(
            "verification_status IS NULL OR verification_status IN "
            "('pending','verified','hash_mismatch','metadata_mismatch','not_found','no_hash_available','error')",
            name="ck_model_verification_status",
        ),
    )


class Runtime(Base):
    __tablename__ = "runtimes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    engine_type: Mapped[str] = mapped_column(String, default="llama.cpp")

    executable_path: Mapped[str] = mapped_column(String, nullable=False)
    model_id: Mapped[Optional[int]] = mapped_column(ForeignKey("models.id", ondelete="SET NULL"))

    host: Mapped[str] = mapped_column(String, default="127.0.0.1")
    port: Mapped[int] = mapped_column(Integer, nullable=False)

    config_json: Mapped[dict] = mapped_column(JSONText, default=dict)

    status: Mapped[str] = mapped_column(String, default=RuntimeStatus.OFFLINE.value)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    source_system: Mapped[str] = mapped_column(String, default="local")
    project_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso, onupdate=_utcnow_iso)

    model: Mapped[Optional["MLModel"]] = relationship(back_populates="runtimes")
    agent_links: Mapped[list["LlmAgentSession"]] = relationship(back_populates="runtime")

    __table_args__ = (
        CheckConstraint(
            "status IN ('OFFLINE','STARTING','ONLINE','STOPPING','ERROR')",
            name="ck_runtime_status",
        ),
        Index("idx_runtimes_status", "status"),
    )


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    agent_type: Mapped[str] = mapped_column(String, default="generic")
    # Which core.agents.AgentAdapter drives this agent's real CLI --
    # "generic" means "just a database label, no real process" (the
    # original behavior); "hermes" etc. means the Settings/terminal panel
    # can actually configure and launch the real thing. Free-text rather
    # than a CHECK constraint since new adapters get added over time.
    agent_backend: Mapped[str] = mapped_column(String, default="generic")

    default_runtime_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("runtimes.id", ondelete="SET NULL")
    )

    config_json: Mapped[dict] = mapped_column(JSONText, default=dict)
    status: Mapped[str] = mapped_column(String, default=AgentStatus.INACTIVE.value)

    source_system: Mapped[str] = mapped_column(String, default="local")
    project_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso, onupdate=_utcnow_iso)

    runtime_links: Mapped[list["LlmAgentSession"]] = relationship(back_populates="agent")

    __table_args__ = (
        CheckConstraint("status IN ('INACTIVE','ACTIVE','ERROR')", name="ck_agent_status"),
    )


class LlmAgentSession(Base):
    """The analytics core: which agent is/was attached to which runtime."""

    __tablename__ = "llm_agent_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    runtime_id: Mapped[int] = mapped_column(ForeignKey("runtimes.id", ondelete="CASCADE"))
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))

    started_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    ended_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default=SessionStatus.ACTIVE.value)

    requests_count: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens_total: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens_total: Mapped[int] = mapped_column(Integer, default=0)
    avg_latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_tokens_per_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    source_system: Mapped[str] = mapped_column(String, default="local")
    project_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    runtime: Mapped["Runtime"] = relationship(back_populates="agent_links")
    agent: Mapped["Agent"] = relationship(back_populates="runtime_links")

    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','COMPLETED','ERROR')", name="ck_session_status"),
        Index("idx_las_runtime", "runtime_id"),
        Index("idx_las_agent", "agent_id"),
    )


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    runtime_id: Mapped[int] = mapped_column(ForeignKey("runtimes.id", ondelete="CASCADE"))
    agent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    session_link_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("llm_agent_sessions.id", ondelete="SET NULL")
    )
    # Nullable: only requests that went through the app's own chat UI have
    # one (external agents hitting the runtime's port directly don't).
    # Added so a chat's requests can be traced to its project -- see
    # core/project_usage.py -- without needing project_id duplicated onto
    # this table too.
    chat_id: Mapped[Optional[int]] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))

    timestamp: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    endpoint: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tokens_per_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ok")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('ok','error')", name="ck_log_status"),
        Index("idx_logs_runtime_time", "runtime_id", "timestamp"),
        Index("idx_logs_agent_time", "agent_id", "timestamp"),
    )


class MetricsSnapshot(Base):
    __tablename__ = "metrics_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    runtime_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("runtimes.id", ondelete="CASCADE"), nullable=True
    )
    timestamp: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    cpu_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ram_used_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ram_total_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gpu_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vram_used_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vram_total_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    tokens_per_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __table_args__ = (Index("idx_metrics_runtime_time", "runtime_id", "timestamp"),)


class Project(Base):
    """A local project: optionally a real folder on disk (`local_path`)
    that the Git panel operates on. `id` is a TEXT uuid4 hex rather than
    an autoincrement int specifically so it's interchangeable with a
    future Synapse-issued project id in the `project_id` TEXT columns
    already sitting on `chats`/`agents`/`runtimes`/etc. -- no migration
    needed there when Synapse starts populating them for real."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    local_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_system: Mapped[str] = mapped_column(String, default="local")

    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso, onupdate=_utcnow_iso)


class Chat(Base):
    """A conversation thread against one runtime (LLM). `agent_id` is
    optional -- a chat can be a direct human<->LLM conversation with no
    agent involved at all, per the requirement that every LLM (agent or
    not) gets its own chat surface."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    runtime_id: Mapped[int] = mapped_column(ForeignKey("runtimes.id", ondelete="CASCADE"))
    agent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String, default="New chat")

    source_system: Mapped[str] = mapped_column(String, default="local")
    project_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso, onupdate=_utcnow_iso)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan", order_by="ChatMessage.id"
    )

    __table_args__ = (
        Index("idx_chats_runtime", "runtime_id"),
        Index("idx_chats_agent", "agent_id"),
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    chat: Mapped["Chat"] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user','assistant','system')", name="ck_message_role"),
        Index("idx_chat_messages_chat", "chat_id", "created_at"),
    )


class OnboardingState(Base):
    __tablename__ = "onboarding_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    llama_cpp_detected: Mapped[int] = mapped_column(Integer, default=0)
    llama_cpp_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    first_model_added: Mapped[int] = mapped_column(Integer, default=0)
    first_runtime_configured: Mapped[int] = mapped_column(Integer, default=0)
    first_agent_created: Mapped[int] = mapped_column(Integer, default=0)
    wizard_completed: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso, onupdate=_utcnow_iso)

    __table_args__ = (CheckConstraint("id = 1", name="ck_onboarding_singleton"),)


class Event(Base):
    """The structured event log (master build prompt section 16) --
    append-only, written only via core/events/bus.py's EventBus, never
    directly. `event_id` is a uuid4 hex kept distinct from the
    autoincrement `id` so an event's identity is stable even across a
    future export/import or cross-device merge; `id` stays the natural
    ordering key for pagination since it's monotonic and index-friendly
    in a way a uuid isn't.

    `runtime_id`/`agent_id`/`session_id` use ON DELETE SET NULL (not
    CASCADE) on purpose: deleting a runtime should not erase the history
    of events that happened while it existed -- an event log that
    disappears the moment its subject is deleted defeats the point of
    having one. `source_*` mirrors the Synapse-facing `source` object
    every event carries, same source_system/project_id/agent_id/
    session_id/task_id shape used elsewhere in this file, defaulting to
    local-only until a real Synapse bridge exists.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    device_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    runtime_id: Mapped[Optional[int]] = mapped_column(ForeignKey("runtimes.id", ondelete="SET NULL"), nullable=True)
    agent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    session_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("llm_agent_sessions.id", ondelete="SET NULL"), nullable=True
    )

    source_system: Mapped[str] = mapped_column(String, default="local")
    source_project_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    metadata_json: Mapped[dict] = mapped_column(JSONText, default=dict)

    __table_args__ = (
        Index("idx_events_type_id", "event_type", "id"),
        Index("idx_events_runtime_id", "runtime_id", "id"),
        Index("idx_events_agent_id", "agent_id", "id"),
    )


class McpServer(Base):
    """One configured MCP (Model Context Protocol) server -- the durable
    half of core/mcp/manager.py, same split as `Runtime` vs. its live
    engine: this row is config + last-known status, the live client
    session only exists in memory while this process runs.

    `env_json` / `headers_json` routinely hold API keys, so the API layer
    (api/mcp.py) NEVER returns their values -- only the key names. Stored
    as-is in the local SQLite file, same trust boundary as the access
    token in local_config.json.

    `name` is unique so a server can be referred to unambiguously in
    events and, later, in the Tool Registry. `source_system/project_id/
    agent_id/session_id` are the same Synapse-ready columns every other
    top-level entity carries.
    """

    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    transport: Mapped[str] = mapped_column(String, nullable=False, default="stdio")
    # stdio
    command: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    args_json: Mapped[dict] = mapped_column(JSONText, default=dict)  # {"args": [...]}; JSONText only round-trips dicts
    env_json: Mapped[dict] = mapped_column(JSONText, default=dict)
    # http / sse
    url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    headers_json: Mapped[dict] = mapped_column(JSONText, default=dict)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String, default="DISCONNECTED")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tools_count: Mapped[int] = mapped_column(Integer, default=0)
    server_info_json: Mapped[dict] = mapped_column(JSONText, default=dict)

    source_system: Mapped[str] = mapped_column(String, default="local")
    project_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso, onupdate=_utcnow_iso)

    __table_args__ = (
        CheckConstraint("transport IN ('stdio','http','sse')", name="ck_mcp_transport"),
        CheckConstraint("status IN ('DISCONNECTED','CONNECTING','CONNECTED','ERROR')", name="ck_mcp_status"),
    )


class PermissionGrant(Base):
    """A recorded permission decision (master build prompt section 20) --
    the durable half of core/permissions/engine.py's live request/response
    flow. A row here is either a standing grant that future checks can
    reuse (allow_session / allow_always, `expires_at IS NULL`) or a
    closed-out record of a one-off decision (allow_once / deny,
    `expires_at` set to `granted_at` -- already expired the moment it's
    written, so it can never silently auto-apply again but still shows up
    in a history/audit view).

    `scope_type`/`scope_key` are deliberately generic strings rather than
    a foreign key to any one table: today the only caller is
    core/mcp/manager.py's tool listings (`mcp_server`: the server's id as
    a string; `mcp_tool`: "<server_id>:<tool name>"), but the engine
    itself has no MCP-specific code, so a future Tool Registry entry (or
    a local-shell tool, etc.) can mint its own scope kind without a
    migration.

    `session_id` uses ON DELETE SET NULL like Event -- deleting a session
    should not erase the history of what was granted during it, but an
    orphaned allow_session row (session_id NULL) can never match a real
    session again, so it's inert rather than dangerous.
    """

    __tablename__ = "permission_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    scope_type: Mapped[str] = mapped_column(String, nullable=False)
    scope_key: Mapped[str] = mapped_column(String, nullable=False)
    risk_level: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    agent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    session_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("llm_agent_sessions.id", ondelete="SET NULL"), nullable=True
    )

    granted_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    # NULL = standing grant, still in force. Non-NULL = closed (either a
    # one-off allow_once/deny record, or a standing grant that was
    # revoked / superseded -- both look the same to a validity check).
    expires_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    source_system: Mapped[str] = mapped_column(String, default="local")
    project_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint("scope_type IN ('mcp_server','mcp_tool')", name="ck_permission_scope_type"),
        CheckConstraint("risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL')", name="ck_permission_risk_level"),
        CheckConstraint(
            "decision IN ('allow_once','allow_session','allow_always','deny')", name="ck_permission_decision"
        ),
        Index("idx_permission_grants_scope", "scope_type", "scope_key", "id"),
    )


# ---------------------------------------------------------------------------
# Engine / Session setup
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = "control_center.sqlite3"


def make_engine(db_path: str = DEFAULT_DB_PATH):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    # Enforce FK constraints on every connection (SQLite defaults them off).
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

# Whether this SQLite build has the FTS5 extension compiled in. Set by
# init_db(); global_search()/search_in_chat() fall back to a plain LIKE
# query when False rather than raising, since FTS5 availability depends
# on how the user's Python was built and isn't something the app can fix.
FTS5_AVAILABLE = False

_FTS_SETUP_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
    content, content='chat_messages', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS chat_messages_ai AFTER INSERT ON chat_messages BEGIN
    INSERT INTO chat_messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS chat_messages_ad AFTER DELETE ON chat_messages BEGIN
    INSERT INTO chat_messages_fts(chat_messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS chat_messages_au AFTER UPDATE ON chat_messages BEGIN
    INSERT INTO chat_messages_fts(chat_messages_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO chat_messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def _fts_quote(query: str) -> str:
    """Wraps user input as a single FTS5 phrase literal so characters that
    are meaningful in FTS5's own query syntax (+, -, *, (), "", AND/OR/NOT)
    can't cause a MATCH syntax error -- found by testing search with a
    query containing '+' (e.g. 'C++'), which otherwise raises
    'fts5: syntax error near "+"'."""
    return '"' + query.replace('"', '""') + '"'


def _setup_fts(engine_) -> bool:
    try:
        with engine_.connect() as conn:
            raw = conn.connection.driver_connection
            # executescript (not repeated exec_driver_sql on a naive ';'
            # split) is required here: a trigger body itself contains
            # semicolons between its statements, so a manual split breaks
            # it into invalid fragments ("incomplete input").
            raw.executescript(_FTS_SETUP_SQL)
            conn.commit()
        return True
    except Exception:
        # Most commonly: this Python's sqlite3 wasn't built with FTS5.
        # Global/per-chat search still works via LIKE -- just slower and
        # without relevance ranking.
        return False


_MODEL_MIGRATION_COLUMNS = [
    ("hf_repo_id", "TEXT"),
    ("hf_filename", "TEXT"),
    ("verification_status", "TEXT"),
    ("verification_checked_at", "TEXT"),
    ("verification_details_json", "TEXT DEFAULT '{}'"),
]

_REQUEST_LOG_MIGRATION_COLUMNS = [
    ("chat_id", "INTEGER"),
]

_AGENT_MIGRATION_COLUMNS = [
    ("agent_backend", "TEXT DEFAULT 'generic'"),
]


def _migrate_models_table(engine_) -> None:
    """Adds the comparison/authenticity-verification columns to an existing
    `models` table for users upgrading from a pre-verification DB.
    `Base.metadata.create_all` only creates missing *tables*, not missing
    *columns* on a table that already exists, so this is a small manual
    migration. Each ALTER TABLE runs in its own try/except so a
    column that's already there (duplicate column name, on a fresh DB
    where create_all already added it) doesn't abort the rest."""
    with engine_.connect() as conn:
        for column_name, column_type in _MODEL_MIGRATION_COLUMNS:
            try:
                conn.execute(text(f"ALTER TABLE models ADD COLUMN {column_name} {column_type}"))
                conn.commit()
            except Exception:
                conn.rollback()


def _migrate_request_logs_table(engine_) -> None:
    """Same pattern as `_migrate_models_table`, for `request_logs.chat_id`
    (added so a project's hardware usage can be estimated from which
    chats/runtimes it actually used -- see core/project_usage.py)."""
    with engine_.connect() as conn:
        for column_name, column_type in _REQUEST_LOG_MIGRATION_COLUMNS:
            try:
                conn.execute(text(f"ALTER TABLE request_logs ADD COLUMN {column_name} {column_type}"))
                conn.commit()
            except Exception:
                conn.rollback()


def _migrate_agents_table(engine_) -> None:
    """Same pattern again, for `agents.agent_backend` (which
    core.agents.AgentAdapter a given agent is actually driven by)."""
    with engine_.connect() as conn:
        for column_name, column_type in _AGENT_MIGRATION_COLUMNS:
            try:
                conn.execute(text(f"ALTER TABLE agents ADD COLUMN {column_name} {column_type}"))
                conn.commit()
            except Exception:
                conn.rollback()


def init_db(engine_=None) -> None:
    """Create all tables + seed the singleton onboarding row."""
    global FTS5_AVAILABLE
    engine_ = engine_ or engine
    Base.metadata.create_all(engine_)
    _migrate_models_table(engine_)
    _migrate_request_logs_table(engine_)
    _migrate_agents_table(engine_)
    FTS5_AVAILABLE = _setup_fts(engine_)
    with SessionLocal() as db:
        if db.get(OnboardingState, 1) is None:
            db.add(OnboardingState(id=1))
            db.commit()


def get_db() -> Iterable[Session]:
    """FastAPI dependency: yields a session, always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Query helpers — onboarding wizard state (singleton row, id=1)
# ---------------------------------------------------------------------------

def get_onboarding_state(db: Session) -> OnboardingState:
    state = db.get(OnboardingState, 1)
    if state is None:  # shouldn't happen once init_db() has run, but don't crash
        state = OnboardingState(id=1)
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def update_onboarding_state(db: Session, **fields) -> OnboardingState:
    state = get_onboarding_state(db)
    for key, value in fields.items():
        if hasattr(state, key):
            setattr(state, key, value)
    db.commit()
    db.refresh(state)
    return state


# ---------------------------------------------------------------------------
# Query helpers — the LLM <-> Agent analytics surface
# ---------------------------------------------------------------------------

def start_llm_agent_session(db: Session, runtime_id: int, agent_id: int) -> LlmAgentSession:
    """Open a new active link between a runtime and an agent.

    Does not close any pre-existing active link for either side — an agent
    can legitimately talk to multiple LLMs, and an LLM can legitimately
    serve multiple agents, at once.
    """
    link = LlmAgentSession(runtime_id=runtime_id, agent_id=agent_id)
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def end_llm_agent_session(db: Session, link_id: int, status: str = SessionStatus.COMPLETED.value) -> Optional[LlmAgentSession]:
    link = db.get(LlmAgentSession, link_id)
    if link is None:
        return None
    link.ended_at = _utcnow_iso()
    link.status = status
    db.commit()
    db.refresh(link)
    return link


def record_request(
    db: Session,
    runtime_id: int,
    agent_id: Optional[int],
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    tokens_per_sec: Optional[float] = None,
    endpoint: str = "/completion",
    session_link_id: Optional[int] = None,
    chat_id: Optional[int] = None,
) -> RequestLog:
    """Log one request AND roll its numbers into the active session link
    (if one is given), so llm_agent_sessions stays an accurate live rollup
    without re-scanning request_logs on every dashboard refresh.
    """
    log = RequestLog(
        runtime_id=runtime_id,
        agent_id=agent_id,
        session_link_id=session_link_id,
        chat_id=chat_id,
        endpoint=endpoint,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        tokens_per_sec=tokens_per_sec,
    )
    db.add(log)

    if session_link_id is not None:
        link = db.get(LlmAgentSession, session_link_id)
        if link is not None:
            n = link.requests_count
            link.requests_count = n + 1
            link.prompt_tokens_total += prompt_tokens
            link.completion_tokens_total += completion_tokens
            # running average, avoids storing every raw latency
            link.avg_latency_ms = (
                latency_ms if n == 0 else ((link.avg_latency_ms or 0) * n + latency_ms) / (n + 1)
            )
            if tokens_per_sec is not None:
                link.avg_tokens_per_sec = (
                    tokens_per_sec if n == 0 else ((link.avg_tokens_per_sec or 0) * n + tokens_per_sec) / (n + 1)
                )

    db.commit()
    db.refresh(log)
    return log


def get_active_agents_for_runtime(db: Session, runtime_id: int) -> list[Agent]:
    """'Which agents are currently using this LLM?' — the exact question
    from the spec (e.g. LLM1 -> [Agent2, Agent3])."""
    return (
        db.query(Agent)
        .join(LlmAgentSession, LlmAgentSession.agent_id == Agent.id)
        .filter(LlmAgentSession.runtime_id == runtime_id, LlmAgentSession.ended_at.is_(None))
        .all()
    )


def get_active_runtimes_for_agent(db: Session, agent_id: int) -> list[Runtime]:
    """The reverse question: which LLM(s) is this agent currently attached to?"""
    return (
        db.query(Runtime)
        .join(LlmAgentSession, LlmAgentSession.runtime_id == Runtime.id)
        .filter(LlmAgentSession.agent_id == agent_id, LlmAgentSession.ended_at.is_(None))
        .all()
    )


def get_active_session_link(db: Session, runtime_id: int, agent_id: int) -> Optional[LlmAgentSession]:
    """The specific active link between one runtime and one agent, if any.
    Used to roll a chat message's usage into the right session row rather
    than guessing from a limited/ordered history query."""
    return (
        db.query(LlmAgentSession)
        .filter(
            LlmAgentSession.runtime_id == runtime_id,
            LlmAgentSession.agent_id == agent_id,
            LlmAgentSession.ended_at.is_(None),
        )
        .first()
    )


def get_current_mapping(db: Session) -> dict[int, list[dict]]:
    """Full 'who's using what right now' snapshot, keyed by runtime_id.

    Returns: {runtime_id: [{"agent_id": ..., "agent_name": ..., "since": ...}, ...]}
    Intended to back a single dashboard/analytics API call.
    """
    active_links = (
        db.query(LlmAgentSession)
        .filter(LlmAgentSession.ended_at.is_(None))
        .all()
    )
    mapping: dict[int, list[dict]] = {}
    for link in active_links:
        mapping.setdefault(link.runtime_id, []).append(
            {
                "agent_id": link.agent_id,
                "agent_name": link.agent.name if link.agent else None,
                "since": link.started_at,
                "requests_count": link.requests_count,
            }
        )
    return mapping


def get_usage_history(
    db: Session, runtime_id: Optional[int] = None, agent_id: Optional[int] = None, limit: int = 100
) -> list[LlmAgentSession]:
    """Historical (including ended) llm<->agent sessions, optionally
    filtered by either side. Powers an analytics/history view."""
    q = db.query(LlmAgentSession)
    if runtime_id is not None:
        q = q.filter(LlmAgentSession.runtime_id == runtime_id)
    if agent_id is not None:
        q = q.filter(LlmAgentSession.agent_id == agent_id)
    return q.order_by(LlmAgentSession.started_at.desc()).limit(limit).all()


def insert_metrics_snapshot(db: Session, runtime_id: Optional[int], **metrics) -> MetricsSnapshot:
    snap = MetricsSnapshot(runtime_id=runtime_id, **metrics)
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


# ---------------------------------------------------------------------------
# Query helpers — the structured event log (core/events/bus.py is the only
# caller of insert_event; everything else should only ever read)
# ---------------------------------------------------------------------------

def insert_event(db: Session, **fields) -> Event:
    ev = Event(**fields)
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def list_events(
    db: Session,
    *,
    event_types: Optional[list[str]] = None,
    type_prefix: Optional[str] = None,
    runtime_id: Optional[int] = None,
    agent_id: Optional[int] = None,
    session_id: Optional[int] = None,
    since: Optional[str] = None,
    before_id: Optional[int] = None,
    limit: int = 100,
) -> list[Event]:
    """Newest-first by default (`id` DESC — monotonic, so this doubles as
    recency order without relying on timestamp string comparison across
    clock adjustments). `before_id` is the pagination cursor: pass the
    smallest `id` seen so far to page further back in time.
    `event_types` and `type_prefix` are ANDed together like every other
    filter here, so pass at most one -- the API layer only ever sends
    one or the other, depending on whether the person typed an exact
    type or a "runtime."-style prefix.
    """
    q = db.query(Event)
    if event_types:
        q = q.filter(Event.event_type.in_(event_types))
    if type_prefix:
        q = q.filter(Event.event_type.like(f"{type_prefix}%"))
    if runtime_id is not None:
        q = q.filter(Event.runtime_id == runtime_id)
    if agent_id is not None:
        q = q.filter(Event.agent_id == agent_id)
    if session_id is not None:
        q = q.filter(Event.session_id == session_id)
    if since is not None:
        q = q.filter(Event.timestamp >= since)
    if before_id is not None:
        q = q.filter(Event.id < before_id)
    return q.order_by(Event.id.desc()).limit(limit).all()


# ---------------------------------------------------------------------------
# Query helpers — chats + search
# ---------------------------------------------------------------------------

def create_chat(db: Session, runtime_id: int, agent_id: Optional[int] = None, title: str = "New chat") -> Chat:
    chat = Chat(runtime_id=runtime_id, agent_id=agent_id, title=title)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def list_chats(
    db: Session,
    runtime_id: Optional[int] = None,
    agent_id: Optional[int] = None,
    project_id: Optional[str] = None,
    unassigned_only: bool = False,
) -> list[Chat]:
    q = db.query(Chat)
    if runtime_id is not None:
        q = q.filter(Chat.runtime_id == runtime_id)
    if agent_id is not None:
        q = q.filter(Chat.agent_id == agent_id)
    if project_id is not None:
        q = q.filter(Chat.project_id == project_id)
    if unassigned_only:
        q = q.filter(Chat.project_id.is_(None))
    return q.order_by(Chat.updated_at.desc()).all()


def delete_chat(db: Session, chat_id: int) -> bool:
    chat = db.get(Chat, chat_id)
    if chat is None:
        return False
    db.delete(chat)  # cascades to chat_messages (and the FTS trigger cleans up the index)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Query helpers — projects
# ---------------------------------------------------------------------------

def create_project(
    db: Session, name: str, local_path: Optional[str] = None, description: Optional[str] = None
) -> Project:
    project = Project(id=uuid.uuid4().hex, name=name, local_path=local_path, description=description)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def list_projects(db: Session) -> list[Project]:
    return db.query(Project).order_by(Project.updated_at.desc()).all()


def update_project(db: Session, project_id: str, **fields) -> Optional[Project]:
    project = db.get(Project, project_id)
    if project is None:
        return None
    for key, value in fields.items():
        if value is not None and hasattr(project, key):
            setattr(project, key, value)
    db.commit()
    db.refresh(project)
    return project


def delete_project(db: Session, project_id: str) -> bool:
    project = db.get(Project, project_id)
    if project is None:
        return False
    # Chats made under this project aren't deleted -- they just fall back
    # to unassigned, same as any other chat, rather than losing history.
    db.query(Chat).filter(Chat.project_id == project_id).update({"project_id": None})
    db.delete(project)
    db.commit()
    return True


def promote_chat_to_project(db: Session, chat_id: int, project_id: str) -> Optional[Chat]:
    chat = db.get(Chat, chat_id)
    if chat is None:
        return None
    chat.project_id = project_id
    db.commit()
    db.refresh(chat)
    return chat


def add_chat_message(
    db: Session,
    chat_id: int,
    role: str,
    content: str,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    latency_ms: Optional[float] = None,
) -> ChatMessage:
    message = ChatMessage(
        chat_id=chat_id,
        role=role,
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
    )
    db.add(message)

    chat = db.get(Chat, chat_id)
    if chat is not None:
        chat.updated_at = _utcnow_iso()
        # A brand-new chat inherits its title from the first user message,
        # so the chat list is scannable without opening every thread.
        if chat.title == "New chat" and role == "user":
            chat.title = content[:60] + ("…" if len(content) > 60 else "")

    db.commit()
    db.refresh(message)
    return message


def get_chat_messages(db: Session, chat_id: int) -> list[ChatMessage]:
    return db.query(ChatMessage).filter(ChatMessage.chat_id == chat_id).order_by(ChatMessage.id).all()


def search_in_chat(db: Session, chat_id: int, query: str, limit: int = 50) -> list[ChatMessage]:
    """Search within one chat. Uses FTS5 MATCH when available for proper
    tokenized matching; falls back to a plain substring LIKE otherwise --
    slower and no relevance ranking, but never breaks the feature."""
    if FTS5_AVAILABLE:
        rows = db.execute(
            text(
                "SELECT chat_messages.id FROM chat_messages_fts "
                "JOIN chat_messages ON chat_messages.id = chat_messages_fts.rowid "
                "WHERE chat_messages.chat_id = :chat_id AND chat_messages_fts MATCH :query "
                "ORDER BY rank LIMIT :limit"
            ),
            {"chat_id": chat_id, "query": _fts_quote(query), "limit": limit},
        ).all()
        ids = [r[0] for r in rows]
        if not ids:
            return []
        messages = db.query(ChatMessage).filter(ChatMessage.id.in_(ids)).all()
        order = {mid: i for i, mid in enumerate(ids)}
        return sorted(messages, key=lambda m: order[m.id])

    like = f"%{query}%"
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.chat_id == chat_id, ChatMessage.content.like(like))
        .order_by(ChatMessage.id.desc())
        .limit(limit)
        .all()
    )


def global_search(db: Session, query: str, limit: int = 50) -> list[dict]:
    """Search across every chat. Returns dicts (not ORM rows) that already
    carry the parent chat's id/title/runtime, since that's what a global
    result needs to link back to the right conversation."""
    if FTS5_AVAILABLE:
        rows = db.execute(
            text(
                "SELECT chat_messages.id FROM chat_messages_fts "
                "JOIN chat_messages ON chat_messages.id = chat_messages_fts.rowid "
                "WHERE chat_messages_fts MATCH :query "
                "ORDER BY rank LIMIT :limit"
            ),
            {"query": _fts_quote(query), "limit": limit},
        ).all()
        ids = [r[0] for r in rows]
        messages = db.query(ChatMessage).filter(ChatMessage.id.in_(ids)).all() if ids else []
        order = {mid: i for i, mid in enumerate(ids)}
        messages.sort(key=lambda m: order[m.id])
    else:
        like = f"%{query}%"
        messages = (
            db.query(ChatMessage)
            .filter(ChatMessage.content.like(like))
            .order_by(ChatMessage.id.desc())
            .limit(limit)
            .all()
        )

    results = []
    for m in messages:
        chat = m.chat
        results.append(
            {
                "message_id": m.id,
                "chat_id": m.chat_id,
                "chat_title": chat.title if chat else None,
                "runtime_id": chat.runtime_id if chat else None,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at,
            }
        )
    return results


# ---------------------------------------------------------------------------
# Query helpers — model comparison + authenticity verification
# ---------------------------------------------------------------------------

def get_models_by_ids(db: Session, model_ids: list[int]) -> list[MLModel]:
    """Fetches models for comparison, preserving the caller's order (the
    order the user picked them in) rather than DB insertion order."""
    rows = {m.id: m for m in db.query(MLModel).filter(MLModel.id.in_(model_ids)).all()}
    return [rows[i] for i in model_ids if i in rows]


def get_model_performance_stats(db: Session, model_id: int) -> dict:
    """Real observed performance for a model, aggregated from
    `request_logs` across every runtime that has ever pointed at it (a
    model can be reattached to a new runtime, e.g. after a restart with
    different launch flags, so this doesn't assume just one). Backs the
    'real-world performance' column of the model comparison feature."""
    from sqlalchemy import func

    runtime_ids = [r.id for r in db.query(Runtime.id).filter(Runtime.model_id == model_id).all()]
    if not runtime_ids:
        return {"requests_count": 0, "avg_latency_ms": None, "avg_tokens_per_sec": None}

    count, avg_latency, avg_tps = (
        db.query(
            func.count(RequestLog.id),
            func.avg(RequestLog.latency_ms),
            func.avg(RequestLog.tokens_per_sec),
        )
        .filter(RequestLog.runtime_id.in_(runtime_ids), RequestLog.status == "ok")
        .one()
    )
    return {
        "requests_count": count or 0,
        "avg_latency_ms": round(avg_latency, 2) if avg_latency is not None else None,
        "avg_tokens_per_sec": round(avg_tps, 2) if avg_tps is not None else None,
    }


def update_model_hf_link(
    db: Session, model_id: int, hf_repo_id: Optional[str], hf_filename: Optional[str] = None
) -> Optional[MLModel]:
    """Points a local model at its Hugging Face source. Required before
    either benchmark lookup or authenticity verification can do anything
    beyond metadata-only, since neither feature guesses a repo from a
    .gguf filename."""
    model = db.get(MLModel, model_id)
    if model is None:
        return None
    model.hf_repo_id = hf_repo_id
    if hf_filename is not None:
        model.hf_filename = hf_filename
    db.commit()
    db.refresh(model)
    return model


def update_model_verification(db: Session, model_id: int, **fields) -> Optional[MLModel]:
    model = db.get(MLModel, model_id)
    if model is None:
        return None
    for key, value in fields.items():
        if hasattr(model, key):
            setattr(model, key, value)
    db.commit()
    db.refresh(model)
    return model


if __name__ == "__main__":
    # `python -m backend.storage.db` -> quick manual sanity check
    init_db()
    print(f"Initialized DB at {DEFAULT_DB_PATH}")
    print(f"FTS5 full-text search available: {FTS5_AVAILABLE}")
