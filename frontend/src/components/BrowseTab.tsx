import { useEffect, useState } from "react";
import { api, apiErrorMessage, type LocalAgentRegistration, type ModelSummary } from "../api";
import { formatBytes } from "../browse-utils";
import { FileBrowserDialog } from "./FileBrowserDialog";
import { OnlineAgentSearch } from "./OnlineAgentSearch";
import { OnlineModelSearch } from "./OnlineModelSearch";
import "../browse.css";

type Mode = "offline" | "online";
type Kind = "models" | "agents";
type Picker = "model-file" | "model-folder" | "agent-file" | null;

/**
 * Settings -> Browse.
 *
 *   Offline: the LLM/agent is already on this machine -- pick it with a
 *            file browser and it's added, no internet involved.
 *   Online:  search trusted sites by an approximate name (Hugging Face for
 *            models, GitHub for agents) and install from there.
 *
 * `onChanged` lets the Settings page refresh its agent/runtime lists
 * after something was added here.
 */
export function BrowseTab({ onChanged }: { onChanged?: () => void }) {
  const [mode, setMode] = useState<Mode>("offline");
  const [kind, setKind] = useState<Kind>("models");
  const [online, setOnline] = useState<boolean | null>(null);
  const [picker, setPicker] = useState<Picker>(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addedModels, setAddedModels] = useState<ModelSummary[] | null>(null);
  const [agentResult, setAgentResult] = useState<LocalAgentRegistration | null>(null);

  useEffect(() => {
    if (mode !== "online") return;
    setOnline(null);
    api.getConnectivity(true).then((r) => setOnline(r.online)).catch(() => setOnline(false));
  }, [mode]);

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
  }

  function switchKind(next: Kind) {
    setKind(next);
    setError(null);
    setAddedModels(null);
    setAgentResult(null);
  }

  async function registerModel(path: string) {
    setPicker(null);
    setBusy(true);
    setError(null);
    setAddedModels(null);
    try {
      setAddedModels(await api.registerLocalModel(path));
      onChanged?.();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not add that."));
    } finally {
      setBusy(false);
    }
  }

  async function registerAgent(path: string) {
    setPicker(null);
    setBusy(true);
    setError(null);
    setAgentResult(null);
    try {
      setAgentResult(await api.registerLocalAgent(path));
      onChanged?.();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not add that."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="browse-toolbar">
        <div className="browse-segmented" role="group" aria-label="Where to look">
          <button className={mode === "offline" ? "active" : ""} onClick={() => switchMode("offline")}>
            Offline (this computer)
          </button>
          <button className={mode === "online" ? "active" : ""} onClick={() => switchMode("online")}>
            Online (search)
          </button>
        </div>
        <div className="browse-segmented" role="group" aria-label="What to look for">
          <button className={kind === "models" ? "active" : ""} onClick={() => switchKind("models")}>
            Models (LLM)
          </button>
          <button className={kind === "agents" ? "active" : ""} onClick={() => switchKind("agents")}>
            Agents (CLI)
          </button>
        </div>
      </div>

      {mode === "offline" && kind === "models" && (
        <div>
          <p className="browse-meta">
            مدل GGUF رو قبلاً روی سیستمت داری؟ فایلش (یا پوشه‌ای که چند مدل توشه) رو انتخاب کن تا به لیست مدل‌ها اضافه بشه.
          </p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="primary" disabled={busy} onClick={() => setPicker("model-file")}>
              Choose a .gguf file…
            </button>
            <button disabled={busy} onClick={() => setPicker("model-folder")}>
              Choose a folder…
            </button>
          </div>
          {busy && <div className="browse-meta">Adding…</div>}
          {addedModels && (
            <div className="browse-card">
              <div className="browse-ok" style={{ marginTop: 0 }}>
                ✓ {addedModels.length} model(s) in your list from that location:
              </div>
              {addedModels.slice(0, 20).map((m) => (
                <div className="browse-file-row" key={m.id}>
                  <span>{m.name}</span>
                  <span className="browse-meta">{formatBytes(m.file_size_bytes)}</span>
                </div>
              ))}
              {addedModels.length > 20 && <div className="browse-meta">…and {addedModels.length - 20} more</div>}
            </div>
          )}
        </div>
      )}

      {mode === "offline" && kind === "agents" && (
        <div>
          <p className="browse-meta">
            Agent (مثلاً Hermes) رو قبلاً نصب کردی؟ فایل اجراییش رو انتخاب کن — حتی اگه روی PATH نباشه. فعلاً فقط agentهایی که برنامه بلده
            کنترلشون کنه شناسایی می‌شن (از روی اسم فایل).
          </p>
          <button className="primary" disabled={busy} onClick={() => setPicker("agent-file")}>
            Choose the agent's executable…
          </button>
          {busy && <div className="browse-meta">Checking…</div>}
          {agentResult && (
            <div className="browse-card">
              <div className="browse-ok" style={{ marginTop: 0 }}>
                ✓ {agentResult.agent.name} {agentResult.created ? "added" : "already in your list — path updated"}
              </div>
              <div className="browse-meta">
                <span className="mono">{agentResult.path}</span>
                {agentResult.detected_version && ` · ${agentResult.detected_version}`}
              </div>
            </div>
          )}
        </div>
      )}

      {mode === "online" && (
        <div>
          {online === false && (
            <div className="browse-banner">این بخش نیاز به اتصال اینترنت دارد. اینترنتت رو چک کن و دوباره تب Online رو باز کن.</div>
          )}
          {kind === "models" && <OnlineModelSearch disabled={online === false} onModelAdded={onChanged} />}
          {kind === "agents" && <OnlineAgentSearch disabled={online === false} onInstalled={onChanged} />}
        </div>
      )}

      {error && <div className="browse-error">{error}</div>}

      {picker === "model-file" && (
        <FileBrowserDialog
          title="Choose a .gguf model file"
          mode="file"
          extensions={[".gguf"]}
          onSelect={(p) => void registerModel(p)}
          onClose={() => setPicker(null)}
        />
      )}
      {picker === "model-folder" && (
        <FileBrowserDialog
          title="Choose a folder with .gguf models"
          mode="folder"
          onSelect={(p) => void registerModel(p)}
          onClose={() => setPicker(null)}
        />
      )}
      {picker === "agent-file" && (
        <FileBrowserDialog
          title="Choose the agent's executable"
          mode="file"
          executableOnly
          onSelect={(p) => void registerAgent(p)}
          onClose={() => setPicker(null)}
        />
      )}
    </div>
  );
}
