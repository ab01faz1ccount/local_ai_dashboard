import { useState } from "react";
import type { ModelSummary, RuntimeMetrics, RuntimeSummary } from "../api";
import { formatTokensPerSec } from "../format";
import { StatusLed } from "./StatusLed";
import { FileBrowserDialog } from "./FileBrowserDialog";

interface RuntimeEditPayload {
  name?: string;
  host?: string;
  port?: number;
  model_id?: number;
  executable_path?: string;
}

interface RuntimeCardProps {
  runtime: RuntimeSummary;
  metrics: RuntimeMetrics | undefined;
  models: ModelSummary[];
  onStart: (id: number) => Promise<void>;
  onStop: (id: number) => Promise<void>;
  onRestart: (id: number) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
  onEdit: (id: number, body: RuntimeEditPayload) => Promise<void>;
}

export function RuntimeCard({ runtime, metrics, models, onStart, onStop, onRestart, onDelete, onEdit }: RuntimeCardProps) {
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [nameDraft, setNameDraft] = useState(runtime.name);
  const [hostDraft, setHostDraft] = useState(runtime.host);
  const [portDraft, setPortDraft] = useState(String(runtime.port));
  const [execPathDraft, setExecPathDraft] = useState(runtime.executable_path ?? "");
  const [modelIdDraft, setModelIdDraft] = useState<number | "">(runtime.model_id ?? "");
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [pickingExecutable, setPickingExecutable] = useState(false);

  async function run(action: (id: number) => Promise<void>) {
    setBusy(true);
    try {
      await action(runtime.id);
    } finally {
      setBusy(false);
    }
  }

  const isOnline = runtime.status === "ONLINE";
  const isTransitioning = runtime.status === "STARTING" || runtime.status === "STOPPING";
  // Host/port/executable/model describe the *next* launch -- editing them
  // while the process is actually running would make the card lie about
  // what's live, same rule the backend enforces.
  const identityFieldsLocked = isOnline || isTransitioning;

  function handleDeleteClick() {
    if (
      window.confirm(
        `«${runtime.name}» حذف بشه؟ همه‌ی چت‌های وابسته به این runtime هم برای همیشه پاک می‌شن (نه فقط لینکشون).`
      )
    ) {
      void run(onDelete);
    }
  }

  function startEditing() {
    setNameDraft(runtime.name);
    setHostDraft(runtime.host);
    setPortDraft(String(runtime.port));
    setExecPathDraft(runtime.executable_path ?? "");
    setModelIdDraft(runtime.model_id ?? "");
    setEditError(null);
    setEditing(true);
  }

  async function saveEdit() {
    setSaving(true);
    setEditError(null);
    const body: RuntimeEditPayload = {};
    if (nameDraft.trim() && nameDraft !== runtime.name) body.name = nameDraft.trim();
    if (!identityFieldsLocked) {
      if (hostDraft.trim() && hostDraft !== runtime.host) body.host = hostDraft.trim();
      const portNum = Number(portDraft);
      if (portDraft.trim() && portNum !== runtime.port) body.port = portNum;
      if (execPathDraft.trim() && execPathDraft !== runtime.executable_path) body.executable_path = execPathDraft.trim();
      if (modelIdDraft !== "" && modelIdDraft !== runtime.model_id) body.model_id = modelIdDraft;
    }
    try {
      await onEdit(runtime.id, body);
      setEditing(false);
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Could not save changes.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="panel runtime-card">
      <div className="runtime-card-header">
        <span className="runtime-name">{runtime.name}</span>
        <StatusLed state={runtime.status} />
      </div>

      <div className="runtime-model">
        {runtime.model_name ?? "no model assigned"} · {runtime.host}:{runtime.port}
      </div>

      {runtime.last_error && <div className="error-text">{runtime.last_error}</div>}

      {editing ? (
        <div className="runtime-edit-form">
          <label className="settings-field">
            <span>Name</span>
            <input value={nameDraft} onChange={(e) => setNameDraft(e.target.value)} />
          </label>
          <label className="settings-field">
            <span>Host {identityFieldsLocked && "(اول متوقفش کن)"}</span>
            <input value={hostDraft} disabled={identityFieldsLocked} onChange={(e) => setHostDraft(e.target.value)} />
          </label>
          <label className="settings-field">
            <span>Port {identityFieldsLocked && "(اول متوقفش کن)"}</span>
            <input
              type="number"
              value={portDraft}
              disabled={identityFieldsLocked}
              onChange={(e) => setPortDraft(e.target.value)}
            />
          </label>
          <label className="settings-field">
            <span>Executable path {identityFieldsLocked && "(اول متوقفش کن)"}</span>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                style={{ flex: 1 }}
                value={execPathDraft}
                disabled={identityFieldsLocked}
                onChange={(e) => setExecPathDraft(e.target.value)}
              />
              <button type="button" disabled={identityFieldsLocked} onClick={() => setPickingExecutable(true)}>
                Browse…
              </button>
            </div>
          </label>
          <label className="settings-field">
            <span>Model {identityFieldsLocked && "(اول متوقفش کن)"}</span>
            <select
              value={modelIdDraft}
              disabled={identityFieldsLocked}
              onChange={(e) => setModelIdDraft(e.target.value === "" ? "" : Number(e.target.value))}
            >
              <option value="">— none —</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>

          {editError && <div className="error-text">{editError}</div>}

          <div className="runtime-actions">
            <button className="primary" disabled={saving} onClick={() => void saveEdit()}>
              {saving ? "Saving…" : "Save"}
            </button>
            <button disabled={saving} onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="runtime-metrics">
            <div>
              <div className="runtime-metric-label">tokens/sec</div>
              <div className="runtime-metric-value mono">{formatTokensPerSec(metrics?.tokens_per_sec)}</div>
            </div>
            <div>
              <div className="runtime-metric-label">active slots</div>
              <div className="runtime-metric-value mono">{metrics?.active_slots ?? "—"}</div>
            </div>
          </div>

          <div className="runtime-actions">
            {!isOnline && (
              <button className="primary" disabled={busy || isTransitioning} onClick={() => run(onStart)}>
                Start
              </button>
            )}
            {isOnline && (
              <button disabled={busy} onClick={() => run(onStop)}>
                Stop
              </button>
            )}
            <button disabled={busy || !isOnline} onClick={() => run(onRestart)}>
              Restart
            </button>
            <button disabled={busy} onClick={startEditing}>
              Edit
            </button>
            <button disabled={busy || isOnline || isTransitioning} onClick={handleDeleteClick}>
              Delete
            </button>
          </div>
        </>
      )}
      {pickingExecutable && (
        <FileBrowserDialog
          title="Choose the llama-server executable"
          mode="file"
          executableOnly
          initialPath={execPathDraft || null}
          onSelect={(path) => {
            setExecPathDraft(path);
            setPickingExecutable(false);
          }}
          onClose={() => setPickingExecutable(false)}
        />
      )}
    </div>
  );
}
