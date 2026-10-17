import { useCallback, useEffect, useState } from "react";
import { api, apiErrorMessage, type AgentMcpProbe, type AgentMcpReport, type AgentSummary } from "../api";
import { detailText, needsConfirm, pendingCount, probeText, stateLabel, summaryText } from "../agent-mcp-utils";

interface Props {
  agent: AgentSummary;
  /** Changes whenever the attached servers change, so the status is re-read. */
  attachedKey: string;
}

/**
 * Writes the servers attached to this agent into the AGENT'S OWN config
 * (Hermes config.yaml, Open Interpreter config.toml, ...) and shows what is
 * really in that file. The app's chat loop needs none of this; an external
 * agent does, because it runs the tools itself.
 */
export function AgentMcpSync({ agent, attachedKey }: Props) {
  const [report, setReport] = useState<AgentMcpReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [probe, setProbe] = useState<(AgentMcpProbe & { for: string }) | null>(null);

  const load = useCallback(async () => {
    try {
      setReport(await api.getAgentMcp(agent.id));
      setError(null);
    } catch (err) {
      setReport(null);
      setError(apiErrorMessage(err, "Could not read this agent's MCP config."));
    }
  }, [agent.id]);

  useEffect(() => {
    void load();
  }, [load, attachedKey]);

  async function sync() {
    setBusy(true);
    setError(null);
    try {
      setReport(await api.syncAgentMcp(agent.id, confirm));
    } catch (err) {
      setError(apiErrorMessage(err, "Could not write this agent's MCP config."));
    } finally {
      setBusy(false);
    }
  }

  async function runProbe(server: string) {
    setBusy(true);
    setError(null);
    try {
      setProbe({ ...(await api.probeAgentMcp(agent.id, server)), for: server });
    } catch (err) {
      setError(apiErrorMessage(err, "Could not check this server with the agent."));
    } finally {
      setBusy(false);
    }
  }

  if (!report) {
    return error ? (
      <div className="agent-mcp-sync">
        <span className="error-text" role="alert">
          {error}
        </span>
      </div>
    ) : null;
  }
  if (report.servers.length === 0 && report.stale.length === 0) return null;

  const pending = pendingCount(report);
  const secrets = needsConfirm(report);

  return (
    <div className="agent-mcp-sync" aria-label={`MCP servers in ${report.agent_name}'s config`}>
      <div className="agent-mcp-head">
        <strong>{summaryText(report)}</strong>
        <span className="muted" title={report.path}>
          {report.path}
        </span>
      </div>
      <ul className="agent-mcp-list">
        {report.servers.map((s) => {
          const label = stateLabel(s.status);
          return (
            <li key={s.server}>
              <span>{s.server}</span>
              <span className={`agent-mcp-state ${label.tone}`}>{label.text}</span>
              {detailText(s) && <span className="muted">{detailText(s)}</span>}
              {report.can_probe && s.status === "in_sync" && (
                <button type="button" className="link-button" disabled={busy} onClick={() => void runProbe(s.server)}>
                  Check with {report.agent_name}
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {secrets && (
        <label className="mcp-inline-check">
          <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
          Write env/header values (API keys) into {report.agent_name}'s config file in plain text
        </label>
      )}
      <div className="agent-mcp-actions">
        <button type="button" disabled={busy || (pending === 0 && !(secrets && confirm))} onClick={() => void sync()}>
          {busy ? "Working…" : `Write to ${report.agent_name}'s config`}
        </button>
        <span className="muted">The original file is copied to a .lai-backup once; your own entries are not touched.</span>
      </div>
      <p className="muted agent-mcp-note">{report.permission_note}</p>
      {probe && (
        <div className="agent-mcp-probe" role="status">
          <strong>{probe.for}:</strong> {probeText(probe)}
          {probe.output && <pre>{probe.output}</pre>}
        </div>
      )}
      {error && (
        <span className="error-text" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
