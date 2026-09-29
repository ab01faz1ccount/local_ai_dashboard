import { useState } from "react";
import { apiErrorMessage, type McpServer, type McpServerPayload } from "../api";
import {
  EMPTY_FORM,
  buildCreatePayload,
  buildUpdatePayload,
  formFromServer,
  type McpFormState,
} from "../mcp-utils";

interface Props {
  /** Present = edit that server; absent = add a new one. */
  server?: McpServer;
  /** Performs the API call. May throw; the message is shown in the form. */
  onSave: (payload: McpServerPayload & { name: string }) => Promise<void>;
  onCancel: () => void;
}

/**
 * Add / edit one MCP server. Two rules shape it:
 *
 * - Secrets are write-only. The backend never returns env/header VALUES,
 *   so an edit form can't (and doesn't pretend to) show them: existing
 *   names appear as removable chips, and the textarea is for entries to
 *   add or replace.
 * - Args are one per line -- exactly one argv entry each -- because the
 *   backend executes them directly, with no shell to re-split anything.
 */
export function McpServerForm({ server, onSave, onCancel }: Props) {
  const [form, setForm] = useState<McpFormState>(server ? formFromServer(server) : EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const editing = server != null;
  const locked = editing && (server.status === "CONNECTED" || server.status === "CONNECTING");
  const isStdio = form.transport === "stdio";

  function set<K extends keyof McpFormState>(key: K, value: McpFormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function toggleRemoved(field: "removedEnvKeys" | "removedHeaderKeys", key: string) {
    setForm((f) => ({
      ...f,
      [field]: f[field].includes(key) ? f[field].filter((k) => k !== key) : [...f[field], key],
    }));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const built = editing ? buildUpdatePayload(form, locked) : buildCreatePayload(form);
    if (!built.ok) {
      setError(built.error);
      return;
    }
    setSaving(true);
    try {
      await onSave(built.value as McpServerPayload & { name: string });
    } catch (err) {
      setError(apiErrorMessage(err, "Could not save the server."));
      setSaving(false);
    }
  }

  const existingKeys = editing ? (isStdio ? server.env_keys : server.header_keys) : [];
  const removedField = isStdio ? "removedEnvKeys" : "removedHeaderKeys";

  return (
    <form className="panel mcp-form" onSubmit={submit} aria-label={editing ? "Edit MCP server" : "Add MCP server"}>
      <h3 className="mcp-form-title">{editing ? `Edit “${server.name}”` : "Add MCP server"}</h3>

      {locked && (
        <p className="muted mcp-form-note">
          This server is connected. Disconnect it to change how it connects; name and description can be edited now.
        </p>
      )}

      <div className="settings-grid">
        <label className="settings-field">
          <span>Name</span>
          <input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="filesystem" />
        </label>
        <label className="settings-field">
          <span>Transport</span>
          <select value={form.transport} disabled={locked} onChange={(e) => set("transport", e.target.value as McpFormState["transport"])}>
            <option value="stdio">stdio (local process)</option>
            <option value="http">http (streamable)</option>
            <option value="sse">sse</option>
          </select>
        </label>
      </div>

      <label className="settings-field">
        <span>Description (optional)</span>
        <input value={form.description} onChange={(e) => set("description", e.target.value)} />
      </label>

      {isStdio ? (
        <>
          <label className="settings-field">
            <span>Command — a launcher (npx, uvx, node, python, …) or a full path to an executable</span>
            <input value={form.command} disabled={locked} onChange={(e) => set("command", e.target.value)} placeholder="npx" />
          </label>
          <label className="settings-field">
            <span>Arguments — one per line</span>
            <textarea
              value={form.argsText}
              disabled={locked}
              rows={4}
              onChange={(e) => set("argsText", e.target.value)}
              placeholder={"-y\n@modelcontextprotocol/server-filesystem\n/path/to/folder"}
            />
          </label>
        </>
      ) : (
        <label className="settings-field">
          <span>URL</span>
          <input value={form.url} disabled={locked} onChange={(e) => set("url", e.target.value)} placeholder="http://127.0.0.1:8000/mcp" />
        </label>
      )}

      {existingKeys.length > 0 && (
        <div className="settings-field">
          <span>{isStdio ? "Saved environment variables (values hidden)" : "Saved headers (values hidden)"}</span>
          <div className="mcp-chips">
            {existingKeys.map((k) => {
              const removed = form[removedField].includes(k);
              return (
                <button
                  key={k}
                  type="button"
                  className="mcp-chip"
                  data-removed={removed}
                  disabled={locked}
                  aria-label={removed ? `Keep ${k}` : `Remove ${k}`}
                  onClick={() => toggleRemoved(removedField, k)}
                >
                  {k} {removed ? "(will be removed — undo)" : "×"}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {isStdio ? (
        <label className="settings-field">
          <span>{editing ? "Add or replace environment variables — KEY=VALUE per line" : "Environment variables (optional) — KEY=VALUE per line"}</span>
          <textarea value={form.envText} disabled={locked} rows={3} onChange={(e) => set("envText", e.target.value)} placeholder="API_KEY=…" />
        </label>
      ) : (
        <label className="settings-field">
          <span>{editing ? "Add or replace headers — Name: value per line" : "Headers (optional) — Name: value per line"}</span>
          <textarea
            value={form.headersText}
            disabled={locked}
            rows={3}
            onChange={(e) => set("headersText", e.target.value)}
            placeholder="Authorization: Bearer …"
          />
        </label>
      )}

      {!editing && (
        <label className="mcp-inline-check">
          <input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          Enabled
        </label>
      )}

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      <div className="runtime-actions">
        <button className="primary" type="submit" disabled={saving}>
          {saving ? "Saving…" : editing ? "Save changes" : "Add server"}
        </button>
        <button type="button" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </form>
  );
}
