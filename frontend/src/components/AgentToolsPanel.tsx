import { useEffect, useState } from "react";
import { api, apiErrorMessage, type AgentSummary, type McpServer } from "../api";

interface Props {
  agent: AgentSummary;
  onUpdated: (agent: AgentSummary) => void;
}

/**
 * Which MCP servers this agent can use tools from -- backs
 * `agent.config_json.mcp_server_ids`, which core/agent_loop.py reads
 * before every turn (only tools from a listed, currently-connected
 * server are ever offered to the model). A server that isn't connected
 * is still shown, greyed, so attaching it ahead of connecting is
 * possible -- its tools simply aren't offered until it's online.
 */
export function AgentToolsPanel({ agent, onUpdated }: Props) {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .listMcpServers()
      .then((list) => {
        if (!cancelled) setServers(list);
      })
      .catch((err) => {
        if (!cancelled) setError(apiErrorMessage(err, "Could not load MCP servers."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const attached = new Set((agent.config_json.mcp_server_ids as number[] | undefined) ?? []);

  async function toggle(serverId: number, checked: boolean) {
    const next = new Set(attached);
    if (checked) next.add(serverId);
    else next.delete(serverId);
    setSaving(serverId);
    setError(null);
    try {
      const updated = await api.updateAgentConfig(agent.id, { config_json: { mcp_server_ids: Array.from(next) } });
      onUpdated(updated);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not update this agent's tools."));
    } finally {
      setSaving(null);
    }
  }

  if (loading) return null;
  if (servers.length === 0) return null;

  return (
    <div className="agent-tools-panel">
      <span className="muted">Tools for {agent.name}:</span>
      {servers.map((s) => (
        <label key={s.id} className="mcp-inline-check" title={s.status !== "CONNECTED" ? "Not connected — attach now, tools apply once it's online" : undefined}>
          <input
            type="checkbox"
            checked={attached.has(s.id)}
            disabled={saving === s.id}
            onChange={(e) => void toggle(s.id, e.target.checked)}
          />
          {s.name}
          {s.status !== "CONNECTED" && <span className="muted"> (offline)</span>}
        </label>
      ))}
      {error && (
        <span className="error-text" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
