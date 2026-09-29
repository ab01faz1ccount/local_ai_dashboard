import { useState } from "react";
import type { McpServer, McpTool } from "../api";
import { commandPreview } from "../mcp-utils";
import { McpStatusLed } from "./McpStatusLed";

export interface ToolsPanelState {
  loading: boolean;
  tools?: McpTool[];
  error?: string;
}

interface Props {
  server: McpServer;
  /** "connecting" | "disconnecting" | "working" while a request for this card is in flight. */
  busy?: string;
  toolsPanel?: ToolsPanelState;
  onConnect: () => void;
  onDisconnect: () => void;
  onToggleEnabled: (enabled: boolean) => void;
  onEdit: () => void;
  onDelete: () => void;
  onToggleTools: () => void;
}

/** One MCP server as a card (the list-of-cards + enable toggle + Add
 * pattern the roadmap takes from Jan). Everything destructive is a
 * two-step in-card confirm rather than window.confirm. */
export function McpServerCard({
  server, busy, toolsPanel, onConnect, onDisconnect, onToggleEnabled, onEdit, onDelete, onToggleTools,
}: Props) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const connected = server.status === "CONNECTED";
  const inFlight = busy != null;
  const info = server.server_info;

  return (
    <div className="panel runtime-card mcp-card" data-testid={`mcp-card-${server.name}`}>
      <div className="runtime-card-header">
        <span className="runtime-name">{server.name}</span>
        <McpStatusLed state={busy === "connecting" ? "CONNECTING" : server.status} />
      </div>

      {server.description && <div className="runtime-model">{server.description}</div>}

      <div className="mcp-meta">
        <span className="mcp-tag">{server.transport}</span>
        {server.is_remote && <span className="mcp-tag" data-kind="remote" title="Connects over the internet, not to this machine">remote</span>}
        {info?.name && (
          <span className="muted mono">
            {info.name}
            {info.version ? ` ${info.version}` : ""}
          </span>
        )}
      </div>

      <div className="mono mcp-command" title="What this server runs">{commandPreview(server)}</div>

      {(server.env_keys.length > 0 || server.header_keys.length > 0) && (
        <div className="muted mcp-secrets">
          {server.env_keys.length > 0 && <span>env: {server.env_keys.join(", ")}</span>}
          {server.header_keys.length > 0 && <span>headers: {server.header_keys.join(", ")}</span>}
          <span> (values hidden)</span>
        </div>
      )}

      {server.status === "ERROR" && server.last_error && (
        <div className="error-text" role="alert">
          {server.last_error}
        </div>
      )}

      <div className="runtime-metrics">
        <div>
          <div className="runtime-metric-label">TOOLS</div>
          <div className="runtime-metric-value">{connected ? server.tools_count : "—"}</div>
        </div>
      </div>

      <label className="mcp-inline-check">
        <input
          type="checkbox"
          checked={server.enabled}
          disabled={inFlight}
          onChange={(e) => onToggleEnabled(e.target.checked)}
          aria-label={`Enable ${server.name}`}
        />
        Enabled
      </label>

      {confirmingDelete ? (
        <div className="runtime-actions">
          <span className="muted">Delete “{server.name}”?</span>
          <button
            className="primary"
            disabled={inFlight}
            onClick={() => {
              setConfirmingDelete(false);
              onDelete();
            }}
          >
            Yes, delete
          </button>
          <button onClick={() => setConfirmingDelete(false)}>Cancel</button>
        </div>
      ) : (
        <div className="runtime-actions">
          {connected ? (
            <button disabled={inFlight} onClick={onDisconnect}>
              {busy === "disconnecting" ? "Disconnecting…" : "Disconnect"}
            </button>
          ) : (
            <button className="primary" disabled={inFlight || !server.enabled} onClick={onConnect} title={server.enabled ? undefined : "Enable this server first"}>
              {busy === "connecting" ? "Connecting…" : "Connect"}
            </button>
          )}
          <button disabled={!connected || inFlight} onClick={onToggleTools}>
            {toolsPanel ? "Hide tools" : "Tools"}
          </button>
          <button disabled={inFlight} onClick={onEdit}>
            Edit
          </button>
          <button disabled={inFlight} onClick={() => setConfirmingDelete(true)}>
            Delete
          </button>
        </div>
      )}

      {toolsPanel && (
        <div className="mcp-tools">
          {toolsPanel.loading && <span className="muted">Loading tools…</span>}
          {toolsPanel.error && <span className="error-text">{toolsPanel.error}</span>}
          {toolsPanel.tools && toolsPanel.tools.length === 0 && <span className="muted">This server exposes no tools.</span>}
          {toolsPanel.tools?.map((t) => {
            const params = Object.keys(((t.input_schema as { properties?: Record<string, unknown> }).properties) ?? {});
            return (
              <div key={t.name} className="mcp-tool">
                <span className="mono">{t.name}</span>
                {params.length > 0 && <span className="muted mono"> ({params.join(", ")})</span>}
                {t.description && <div className="muted">{t.description}</div>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
