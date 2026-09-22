-- =====================================================================
-- Local AI Control Center — Database Schema (SQLite)
-- v0.1
--
-- Design principles:
--   1. Every "top-level entity" table (runtimes, models, agents, sessions)
--      carries the Synapse-ready `source_*` columns, defaulting to local-only
--      values. This lets Synapse populate them later with zero migration.
--   2. Engine-specific config lives in a JSON TEXT column (`config_json`)
--      rather than as rigid columns, so adding new llama.cpp flags later
--      never requires a schema migration.
--   3. `llm_agent_sessions` is the heart of the analytics requirement:
--      it is the many-to-many link that answers "which agent is using
--      which LLM right now / historically".
--   4. `metrics_snapshots` is an append-only time series, kept separate
--      from the "current state" tables so it can be pruned/aggregated
--      independently without touching operational data.
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- app_settings: single-row-per-key config store
--   Holds things like the local access token, bind host/port, and
--   onboarding wizard progress. Avoids a separate config file that can
--   drift from the DB.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,          -- store as TEXT; parse JSON if needed
    updated_at  TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ---------------------------------------------------------------------
-- models: scanned .gguf files available to load
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS models (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    file_path       TEXT NOT NULL UNIQUE,
    file_size_bytes INTEGER NOT NULL,
    format          TEXT NOT NULL DEFAULT 'gguf',

    -- optional metadata (populated opportunistically, never blocks scanning)
    quantization    TEXT,               -- e.g. "Q4_K_M"
    param_count     TEXT,               -- e.g. "7B" (string: precision varies)
    context_length  INTEGER,            -- model's trained/native ctx, if known

    -- Synapse-ready fields
    source_system   TEXT NOT NULL DEFAULT 'local',   -- 'local' | 'synapse'
    project_id      TEXT,
    agent_id        TEXT,
    session_id      TEXT,

    added_at        TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ---------------------------------------------------------------------
-- runtimes: a configured/running llama.cpp (or future engine) instance
--   One row per "runtime configuration" — can be started/stopped many
--   times; `status` reflects the current state.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runtimes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    engine_type     TEXT NOT NULL DEFAULT 'llama.cpp',  -- future: 'vllm', 'ollama', etc.

    executable_path TEXT NOT NULL,       -- path to llama-server binary
    model_id        INTEGER REFERENCES models(id) ON DELETE SET NULL,

    host            TEXT NOT NULL DEFAULT '127.0.0.1',
    port            INTEGER NOT NULL,

    -- full llama-server launch config as JSON, e.g.:
    -- {"ctx_size": 8192, "n_gpu_layers": 35, "threads": 8, "batch_size": 512,
    --  "parallel_slots": 1, "embedding": false, "temperature": 0.8, ...}
    config_json     TEXT NOT NULL DEFAULT '{}',

    status          TEXT NOT NULL DEFAULT 'OFFLINE'
                        CHECK (status IN ('OFFLINE','STARTING','ONLINE','STOPPING','ERROR')),
    last_error      TEXT,
    pid             INTEGER,

    -- Synapse-ready fields
    source_system   TEXT NOT NULL DEFAULT 'local',
    project_id      TEXT,
    agent_id        TEXT,
    session_id      TEXT,

    created_at      TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_runtimes_status ON runtimes(status);

-- ---------------------------------------------------------------------
-- agents: an AI agent definition (Hermes-style or generic)
--   Kept intentionally generic at MVP — full agent runtime (tools,
--   permissions, execution traces) is Future Roadmap, but the table
--   itself is created now so llm_agent_sessions has something to
--   reference from day one.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT,
    agent_type      TEXT NOT NULL DEFAULT 'generic',  -- future: 'hermes', 'coder', ...

    -- default runtime this agent prefers, if any (can be overridden per session)
    default_runtime_id INTEGER REFERENCES runtimes(id) ON DELETE SET NULL,

    config_json     TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'INACTIVE'
                        CHECK (status IN ('INACTIVE','ACTIVE','ERROR')),

    -- Synapse-ready fields
    source_system   TEXT NOT NULL DEFAULT 'local',
    project_id      TEXT,
    session_id      TEXT,

    created_at      TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ---------------------------------------------------------------------
-- llm_agent_sessions: THE analytics core
--   Many-to-many link between a runtime (LLM instance) and an agent,
--   scoped to a time window. A currently-active link has ended_at = NULL.
--   This is what answers: "LLM1 is currently serving Agent2 and Agent3".
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_agent_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    runtime_id      INTEGER NOT NULL REFERENCES runtimes(id) ON DELETE CASCADE,
    agent_id        INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,

    started_at      TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now')),
    ended_at        TEXT,                 -- NULL => still active/in-progress
    status          TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE','COMPLETED','ERROR')),

    -- rolling counters for this session window (updated as requests happen)
    requests_count      INTEGER NOT NULL DEFAULT 0,
    prompt_tokens_total  INTEGER NOT NULL DEFAULT 0,
    completion_tokens_total INTEGER NOT NULL DEFAULT 0,
    avg_latency_ms       REAL,
    avg_tokens_per_sec   REAL,

    -- Synapse-ready fields
    source_system   TEXT NOT NULL DEFAULT 'local',
    project_id      TEXT,
    session_id      TEXT
);

CREATE INDEX IF NOT EXISTS idx_las_runtime ON llm_agent_sessions(runtime_id);
CREATE INDEX IF NOT EXISTS idx_las_agent ON llm_agent_sessions(agent_id);
-- Fast lookup of "who's active right now" (partial index)
CREATE INDEX IF NOT EXISTS idx_las_active ON llm_agent_sessions(runtime_id, agent_id)
    WHERE ended_at IS NULL;

-- ---------------------------------------------------------------------
-- request_logs: lightweight per-request log (optional, prunable)
--   Not a full event-sourcing system (that's Future Roadmap) — just
--   enough granularity to compute the analytics above and show a
--   recent-activity feed. Old rows can be deleted/aggregated freely;
--   llm_agent_sessions holds the durable rollups.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS request_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    runtime_id      INTEGER NOT NULL REFERENCES runtimes(id) ON DELETE CASCADE,
    agent_id        INTEGER REFERENCES agents(id) ON DELETE SET NULL,  -- NULL = direct/manual use
    session_link_id INTEGER REFERENCES llm_agent_sessions(id) ON DELETE SET NULL,

    timestamp       TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now')),
    endpoint        TEXT,                 -- e.g. '/completion', '/embedding'
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    latency_ms      REAL,
    tokens_per_sec  REAL,
    status          TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','error')),
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_logs_runtime_time ON request_logs(runtime_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_logs_agent_time ON request_logs(agent_id, timestamp);

-- ---------------------------------------------------------------------
-- metrics_snapshots: hardware/runtime telemetry time series
--   Pushed via WebSocket every 1-2s, but only persisted at a coarser
--   interval (e.g. every 10-30s) to keep the table small at MVP scale.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    runtime_id      INTEGER REFERENCES runtimes(id) ON DELETE CASCADE,  -- NULL = system-wide snapshot
    timestamp       TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now')),

    cpu_percent     REAL,
    ram_used_mb     REAL,
    ram_total_mb    REAL,
    gpu_percent     REAL,          -- NULL when no GPU present
    vram_used_mb    REAL,
    vram_total_mb   REAL,

    tokens_per_sec  REAL,          -- from llama.cpp's own reporting, if active
    last_latency_ms REAL
);

CREATE INDEX IF NOT EXISTS idx_metrics_runtime_time ON metrics_snapshots(runtime_id, timestamp);

-- ---------------------------------------------------------------------
-- onboarding_state: tracks setup-wizard progress
--   Lets the frontend resume the wizard where the user left off, and
--   lets the dashboard skip the wizard entirely once complete.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_state (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),  -- single row
    llama_cpp_detected  INTEGER NOT NULL DEFAULT 0,   -- boolean 0/1
    llama_cpp_path      TEXT,
    first_model_added   INTEGER NOT NULL DEFAULT 0,
    first_runtime_configured INTEGER NOT NULL DEFAULT 0,
    first_agent_created INTEGER NOT NULL DEFAULT 0,
    wizard_completed    INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now'))
);

INSERT OR IGNORE INTO onboarding_state (id) VALUES (1);

-- ---------------------------------------------------------------------
-- chats / chat_messages: conversation history per LLM (agent optional)
--   A chat's agent_id is nullable -- every LLM gets a chat surface
--   whether or not an agent is attached to it.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    runtime_id      INTEGER NOT NULL REFERENCES runtimes(id) ON DELETE CASCADE,
    agent_id        INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    title           TEXT NOT NULL DEFAULT 'New chat',

    source_system   TEXT NOT NULL DEFAULT 'local',
    project_id      TEXT,
    session_id      TEXT,

    created_at      TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_chats_runtime ON chats(runtime_id);
CREATE INDEX IF NOT EXISTS idx_chats_agent ON chats(agent_id);

CREATE TABLE IF NOT EXISTS chat_messages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id             INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role                TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content             TEXT NOT NULL,
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    latency_ms          REAL,
    created_at          TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_chat ON chat_messages(chat_id, created_at);

-- Full-text search over message content (per-chat and global), kept in
-- sync via triggers. The app checks at startup whether FTS5 is compiled
-- into this Python's sqlite3 build and falls back to a plain LIKE query
-- if not -- see backend/storage/db.py's FTS5_AVAILABLE flag.
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
