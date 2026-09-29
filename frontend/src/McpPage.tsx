import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiErrorMessage, type McpServer, type McpServerPayload } from "./api";
import { McpServerCard, type ToolsPanelState } from "./components/McpServerCard";
import { McpServerForm } from "./components/McpServerForm";

const POLL_MS = 5000; // catches a server that died on its own (backend flips it to ERROR)

/**
 * MCP Servers: the list of configured Model Context Protocol servers with
 * connect/disconnect, enable toggle, tool listing, add/edit/delete.
 *
 * This page only manages CONNECTIONS. There is deliberately no "run this
 * tool" button: executing tools needs the Permission Engine first (see
 * PROJECT_STATUS_AND_ROADMAP.md, next phases).
 */
export function McpPage() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<"new" | number | null>(null);
  const [busy, setBusy] = useState<Record<number, string>>({});
  const [tools, setTools] = useState<Record<number, ToolsPanelState>>({});
  const busyRef = useRef(busy);
  busyRef.current = busy;

  const refresh = useCallback(async (silent: boolean) => {
    try {
      const list = await api.listMcpServers();
      setServers(list);
      // A tools panel for a server that's no longer connected would show stale data.
      setTools((prev) => {
        const next = { ...prev };
        for (const id of Object.keys(next).map(Number)) {
          if (list.find((s) => s.id === id)?.status !== "CONNECTED") delete next[id];
        }
        return next;
      });
      if (!silent) setError(null);
    } catch (err) {
      if (!silent) setError(apiErrorMessage(err, "Could not load MCP servers."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh(false);
    const timer = setInterval(() => {
      if (Object.keys(busyRef.current).length === 0) void refresh(true);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  function replaceServer(updated: McpServer) {
    setServers((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
  }

  async function withBusy(id: number, label: string, work: () => Promise<void>) {
    setBusy((b) => ({ ...b, [id]: label }));
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(apiErrorMessage(err, "That didn't work."));
      await refresh(true);
    } finally {
      setBusy((b) => {
        const { [id]: _drop, ...rest } = b;
        return rest;
      });
    }
  }

  const connect = (s: McpServer) =>
    withBusy(s.id, "connecting", async () => {
      const result = await api.connectMcpServer(s.id);
      replaceServer(result.server); // a failed connect is a normal result: the card shows last_error
    });

  const disconnect = (s: McpServer) =>
    withBusy(s.id, "disconnecting", async () => {
      const result = await api.disconnectMcpServer(s.id);
      replaceServer(result.server);
      setTools(({ [s.id]: _drop, ...rest }) => rest);
    });

  const toggleEnabled = (s: McpServer, enabled: boolean) =>
    withBusy(s.id, "working", async () => {
      replaceServer(await api.updateMcpServer(s.id, { enabled }));
    });

  const remove = (s: McpServer) =>
    withBusy(s.id, "working", async () => {
      await api.deleteMcpServer(s.id);
      setServers((prev) => prev.filter((x) => x.id !== s.id));
      setTools(({ [s.id]: _drop, ...rest }) => rest);
    });

  async function toggleTools(s: McpServer) {
    if (tools[s.id]) {
      setTools(({ [s.id]: _drop, ...rest }) => rest);
      return;
    }
    setTools((t) => ({ ...t, [s.id]: { loading: true } }));
    try {
      const res = await api.listMcpServerTools(s.id);
      setTools((t) => ({ ...t, [s.id]: { loading: false, tools: res.tools } }));
    } catch (err) {
      setTools((t) => ({ ...t, [s.id]: { loading: false, error: apiErrorMessage(err, "Could not list tools.") } }));
    }
  }

  async function save(payload: McpServerPayload & { name: string }) {
    if (form === "new") {
      const created = await api.createMcpServer(payload);
      setServers((prev) => [...prev, created]);
    } else if (typeof form === "number") {
      replaceServer(await api.updateMcpServer(form, payload));
    }
    setForm(null);
  }

  const editing = typeof form === "number" ? servers.find((s) => s.id === form) : undefined;

  return (
    <div className="main">
      <div className="mcp-header">
        <div>
          <h2 className="section-heading" style={{ margin: 0 }}>
            MCP Servers
          </h2>
          <p className="muted mcp-subtitle">
            Model Context Protocol servers give agents tools. Connecting only lists what a server offers — nothing is
            executed from this page.
          </p>
        </div>
        {form === null && (
          <button className="primary" onClick={() => setForm("new")}>
            Add server
          </button>
        )}
      </div>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {form === "new" && <McpServerForm key="new" onSave={save} onCancel={() => setForm(null)} />}
      {editing && <McpServerForm key={editing.id} server={editing} onSave={save} onCancel={() => setForm(null)} />}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : servers.length === 0 && form === null ? (
        <div className="panel empty-state">
          <div>No MCP servers yet.</div>
          <div className="muted">
            Add one — for example a stdio server started with <span className="mono">npx</span> or{" "}
            <span className="mono">uvx</span> — then connect to see its tools.
          </div>
          <button className="primary" onClick={() => setForm("new")}>
            Add your first server
          </button>
        </div>
      ) : (
        <div className="runtime-grid">
          {servers.map((s) => (
            <McpServerCard
              key={s.id}
              server={s}
              busy={busy[s.id]}
              toolsPanel={tools[s.id]}
              onConnect={() => void connect(s)}
              onDisconnect={() => void disconnect(s)}
              onToggleEnabled={(v) => void toggleEnabled(s, v)}
              onEdit={() => setForm(s.id)}
              onDelete={() => void remove(s)}
              onToggleTools={() => void toggleTools(s)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
