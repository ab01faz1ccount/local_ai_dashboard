import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  apiErrorMessage,
  connectEventsSocket,
  type AgentSummary,
  type EventTypeInfo,
  type LogEvent,
  type RuntimeSummary,
} from "./api";
import { EventBadge } from "./components/EventBadge";
import { formatClockTime, formatFullTimestamp } from "./format";

const PAGE_SIZE = 50;
const MAX_IN_MEMORY = 500; // live events are capped, not archived here -- history is a re-fetch away

/**
 * Read-only view of core/events/: a live tail (GET /ws/events) merged
 * with paginated history (GET /api/v1/events), the same event log every
 * runtime.started/model.loaded/agent.created/... call in the backend
 * writes to. Nothing here writes anything -- this page is a window onto
 * an audit trail, not a control surface.
 */
export function LogsPage() {
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(true);

  const [eventTypes, setEventTypes] = useState<EventTypeInfo[]>([]);
  const [runtimes, setRuntimes] = useState<RuntimeSummary[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);

  const [typeFilter, setTypeFilter] = useState<string>("");
  const [runtimeFilter, setRuntimeFilter] = useState<number | "">("");
  const [agentFilter, setAgentFilter] = useState<number | "">("");

  // Guards a fetch that's no longer relevant (filters changed while it was in flight).
  const requestSeq = useRef(0);

  const runtimeName = useMemo(() => new Map(runtimes.map((r) => [r.id, r.name])), [runtimes]);
  const agentName = useMemo(() => new Map(agents.map((a) => [a.id, a.name])), [agents]);

  useEffect(() => {
    api.getEventTypes().then(setEventTypes).catch(() => undefined);
    api.listRuntimes().then(setRuntimes).catch(() => undefined);
    api.listAgents().then(setAgents).catch(() => undefined);
  }, []);

  const loadFirstPage = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    try {
      const page = await api.listEvents({
        eventType: typeFilter || undefined,
        runtimeId: runtimeFilter === "" ? undefined : runtimeFilter,
        agentId: agentFilter === "" ? undefined : agentFilter,
        limit: PAGE_SIZE,
      });
      if (seq !== requestSeq.current) return;
      setEvents(page);
      setHasMore(page.length === PAGE_SIZE);
    } catch (err) {
      if (seq !== requestSeq.current) return;
      setError(apiErrorMessage(err, "Could not load events."));
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [typeFilter, runtimeFilter, agentFilter]);

  useEffect(() => {
    void loadFirstPage();
  }, [loadFirstPage]);

  async function loadMore() {
    if (events.length === 0) return;
    setLoadingMore(true);
    try {
      const oldestId = events[events.length - 1].id;
      const page = await api.listEvents({
        eventType: typeFilter || undefined,
        runtimeId: runtimeFilter === "" ? undefined : runtimeFilter,
        agentId: agentFilter === "" ? undefined : agentFilter,
        beforeId: oldestId,
        limit: PAGE_SIZE,
      });
      setEvents((prev) => [...prev, ...page]);
      setHasMore(page.length === PAGE_SIZE);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not load more events."));
    } finally {
      setLoadingMore(false);
    }
  }

  // Live tail: always connected, but only touches the list while `live`
  // is on and the incoming event matches the current filters -- pausing
  // (to read something) doesn't drop the connection, it just stops
  // prepending, so resuming doesn't need to reconnect or backfill.
  useEffect(() => {
    const cleanup = connectEventsSocket((ev) => {
      if (!live) return;
      if (typeFilter) {
        const matches = typeFilter.endsWith(".") ? ev.event_type.startsWith(typeFilter) : ev.event_type === typeFilter;
        if (!matches) return;
      }
      if (runtimeFilter !== "" && ev.runtime_id !== runtimeFilter) return;
      if (agentFilter !== "" && ev.agent_id !== agentFilter) return;
      setEvents((prev) => [ev, ...prev].slice(0, MAX_IN_MEMORY));
    });
    return cleanup;
  }, [live, typeFilter, runtimeFilter, agentFilter]);

  const groupedTypes = useMemo(() => groupByCategory(eventTypes), [eventTypes]);

  return (
    <div className="main">
      <section>
        <div className="section-heading">
          <h2>Logs</h2>
          <button onClick={() => setLive((v) => !v)}>{live ? "⏸ Pause" : "▶ Resume"}</button>
        </div>

        <div className="logs-toolbar">
          <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
            <option value="">All event types</option>
            {groupedTypes.map(([category, items]) => (
              <optgroup key={category} label={category}>
                <option value={`${category}.`}>All {category}.*</option>
                {items.map((t) => (
                  <option key={t.event_type} value={t.event_type} title={t.description}>
                    {t.event_type}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>

          <select
            value={runtimeFilter}
            onChange={(e) => setRuntimeFilter(e.target.value === "" ? "" : Number(e.target.value))}
          >
            <option value="">All runtimes</option>
            {runtimes.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>

          <select value={agentFilter} onChange={(e) => setAgentFilter(e.target.value === "" ? "" : Number(e.target.value))}>
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>

          {(typeFilter || runtimeFilter !== "" || agentFilter !== "") && (
            <button
              onClick={() => {
                setTypeFilter("");
                setRuntimeFilter("");
                setAgentFilter("");
              }}
            >
              Clear filters
            </button>
          )}
        </div>

        {error && <div className="error-text">{error}</div>}

        {loading ? (
          <p className="muted">Loading…</p>
        ) : events.length === 0 ? (
          <p className="muted">No events yet -- start a runtime or send a chat message to see something here.</p>
        ) : (
          <>
            <table className="models-table logs-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Event</th>
                  <th>Runtime</th>
                  <th>Agent</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {events.map((ev) => (
                  <tr key={ev.event_id}>
                    <td className="mono" title={formatFullTimestamp(ev.timestamp)}>
                      {formatClockTime(ev.timestamp)}
                    </td>
                    <td>
                      <EventBadge eventType={ev.event_type} />
                    </td>
                    <td className="muted">
                      {ev.runtime_id != null
                        ? runtimeName.get(ev.runtime_id) ?? `#${ev.runtime_id}`
                        : deletedEntityFallback(ev, "runtime.")}
                    </td>
                    <td className="muted">
                      {ev.agent_id != null
                        ? agentName.get(ev.agent_id) ?? `#${ev.agent_id}`
                        : deletedEntityFallback(ev, "agent.")}
                    </td>
                    <td className="mono logs-details">{formatMetadata(ev.metadata)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {hasMore && (
              <button disabled={loadingMore} onClick={() => void loadMore()}>
                {loadingMore ? "Loading…" : "Load older"}
              </button>
            )}
          </>
        )}
      </section>
    </div>
  );
}

/** event_type "runtime.started" -> "runtime"; groups the /events/types
 * response into optgroups for the filter dropdown. */
function groupByCategory(types: EventTypeInfo[]): [string, EventTypeInfo[]][] {
  const groups = new Map<string, EventTypeInfo[]>();
  for (const t of types) {
    const category = t.event_type.split(".")[0];
    const list = groups.get(category) ?? [];
    list.push(t);
    groups.set(category, list);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

/** A compact "key=value, key=value" line -- readable at a glance in a
 * table cell without a JSON viewer for what's almost always 1-3 fields. */
function formatMetadata(metadata: Record<string, unknown>): string {
  const entries = Object.entries(metadata).filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (entries.length === 0) return "—";
  // " · " (not a plain double space): HTML collapses runs of whitespace
  // visually anyway, so a bare double space would render identically to
  // a single one -- a real, visible separator actually reads as several
  // fields rather than one run-on string.
  return entries.map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ");
}

/** When a runtime.___ / agent.___ event's own runtime_id/agent_id is
 * null (the entity it was about has since been deleted -- ON DELETE SET
 * NULL), fall back to the name captured in metadata at emission time.
 * Gated on the event type's own prefix so this never leaks, say, an
 * agent's name into the Runtime column for an unrelated agent.* event
 * that simply has no runtime_id (it was never about one to begin with). */
function deletedEntityFallback(ev: LogEvent, prefix: string): string {
  if (!ev.event_type.startsWith(prefix)) return "—";
  const name = ev.metadata["name"];
  return typeof name === "string" ? name : "—";
}
