import { useCallback, useEffect, useState } from "react";
import { api, apiErrorMessage, type SessionDetail, type SessionExportFormat, type SessionSummary } from "./api";
import { downloadTextFile, formatDuration, toolCallStats, totalTokens } from "./session-utils";
import { formatMetric } from "./analytics-utils";

const FORMATS: { id: SessionExportFormat; label: string }[] = [
  { id: "json", label: "JSON" },
  { id: "jsonl", label: "JSONL" },
  { id: "markdown", label: "Markdown" },
];

/**
 * Sessions (master build prompt section 22): every agent<->runtime link
 * as a first-class record -- cumulative tokens/latency, which chats it
 * touched, how many tool calls it made -- with export to JSON/JSONL/
 * Markdown. The Markdown export doubles as a readable trace of what the
 * agent actually did.
 */
export function SessionsPage() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeOnly, setActiveOnly] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [exporting, setExporting] = useState<SessionExportFormat | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSessions(await api.listSessions({ activeOnly }));
      setError(null);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not load sessions."));
    } finally {
      setLoading(false);
    }
  }, [activeOnly]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (selectedId == null) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    api
      .getSession(selectedId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((err) => {
        if (!cancelled) setError(apiErrorMessage(err, "Could not load that session."));
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  async function doExport(format: SessionExportFormat) {
    if (selectedId == null) return;
    setExporting(format);
    try {
      const { filename, content } = await api.exportSession(selectedId, format);
      downloadTextFile(filename, content);
    } catch (err) {
      setError(apiErrorMessage(err, "Export failed."));
    } finally {
      setExporting(null);
    }
  }

  const tools = detail ? toolCallStats(detail.event_counts) : null;

  return (
    <div className="main">
      <div className="section-heading">
        <h2>Sessions</h2>
      </div>
      <p className="muted mcp-subtitle">
        Each time an agent is attached to a runtime, that's a session. Select one to see what happened and export it.
      </p>

      <div className="logs-toolbar">
        <label className="mcp-inline-check">
          <input type="checkbox" checked={activeOnly} onChange={(e) => setActiveOnly(e.target.checked)} />
          Active only
        </label>
      </div>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : sessions.length === 0 ? (
        <div className="panel empty-state">
          No {activeOnly ? "active " : ""}sessions yet — attach an agent to a runtime to start one.
        </div>
      ) : (
        <div className="panel permission-grants-table">
          {sessions.map((s) => (
            <button
              key={s.id}
              className="session-row"
              data-testid={`session-${s.id}`}
              data-selected={s.id === selectedId}
              onClick={() => setSelectedId(s.id === selectedId ? null : s.id)}
            >
              <span>
                #{s.id} · {s.agent_name ?? `agent ${s.agent_id}`} → {s.runtime_name ?? `runtime ${s.runtime_id}`}
              </span>
              <span className="mcp-tag">{s.status}</span>
              <span className="muted">{formatDuration(s.started_at, s.ended_at)}</span>
              <span className="muted mono">
                {s.requests_count} req · {totalTokens(s)} tok
              </span>
            </button>
          ))}
        </div>
      )}

      {detail && selectedId === detail.id && (
        <div className="panel session-detail" aria-label={`Session ${detail.id} detail`}>
          <h3 className="mcp-form-title">Session #{detail.id}</h3>
          <div className="session-stats">
            <div>
              <div className="runtime-metric-label">REQUESTS</div>
              <div className="runtime-metric-value">{detail.requests_count}</div>
            </div>
            <div>
              <div className="runtime-metric-label">PROMPT TOKENS</div>
              <div className="runtime-metric-value">{detail.prompt_tokens_total}</div>
            </div>
            <div>
              <div className="runtime-metric-label">COMPLETION TOKENS</div>
              <div className="runtime-metric-value">{detail.completion_tokens_total}</div>
            </div>
            <div>
              <div className="runtime-metric-label">AVG LATENCY</div>
              <div className="runtime-metric-value">{formatMetric(detail.avg_latency_ms, " ms", 0)}</div>
            </div>
            <div>
              <div className="runtime-metric-label">AVG SPEED</div>
              <div className="runtime-metric-value">{formatMetric(detail.avg_tokens_per_sec, " t/s")}</div>
            </div>
          </div>

          {tools && (
            <p className="muted" data-testid="tool-call-summary">
              Tool calls: {tools.called} made · {tools.completed} completed · {tools.failed} failed
            </p>
          )}
          <p className="muted">
            Chats: {detail.chat_ids.length === 0 ? "none recorded" : detail.chat_ids.map((id) => `#${id}`).join(", ")}
          </p>

          <div className="runtime-actions">
            {FORMATS.map((f) => (
              <button key={f.id} disabled={exporting != null} onClick={() => void doExport(f.id)}>
                {exporting === f.id ? "Exporting…" : `Export ${f.label}`}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
