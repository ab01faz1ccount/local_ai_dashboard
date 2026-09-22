# Synapse Integration (contract stub — no code yet)

This folder intentionally holds no implementation. It exists so the future
Synapse integration has a home without requiring a restructure.

Planned contract (Future Roadmap — do not implement yet):

- `register_runtime(runtime_id, project_id, agent_id)` — Synapse announces
  it wants to attach to a local runtime.
- `report_events(event_type, payload)` — push `runtime.started`,
  `agent.stopped`, etc. once the full event bus exists.
- Auth flow for Synapse <-> Control Center communication.
- Full `source_system="synapse"` wiring through Agents/Sessions (the DB
  columns already exist — see backend/storage/schema.sql).

Until then, Synapse (or anything else) can read local state read-only via:
- `GET /api/v1/runtimes`
- `GET /api/v1/models`
