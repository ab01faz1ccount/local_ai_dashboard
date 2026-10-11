import { useEffect, useState } from "react";
import {
  api,
  apiErrorMessage,
  type AgentSummary,
  type LaunchPlan,
  type ModelSummary,
  type QuickConnectResult,
} from "../api";
import { quickConnectMessage } from "../connect-utils";
import { FitBadge } from "./FitBadge";

/**
 * Pick a model (and, optionally, an agent) and press one button: the app picks
 * settings that fit this machine, starts llama-server, and points the agent at
 * it. "Preview" shows the choices without doing anything.
 */
export function QuickConnectPanel({ onConnected }: { onConnected?: () => void }) {
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [modelId, setModelId] = useState<number | "">("");
  const [agentId, setAgentId] = useState<number | "">("");
  const [plan, setPlan] = useState<LaunchPlan | null>(null);
  const [result, setResult] = useState<QuickConnectResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listModels().then(setModels).catch(() => undefined);
    api.listAgents().then((all) => setAgents(all.filter((a) => a.agent_backend !== "generic"))).catch(() => undefined);
  }, []);

  async function preview() {
    if (modelId === "") return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setPlan(await api.launchPlan(modelId, agentId !== ""));
    } catch (err) {
      setPlan(null);
      setError(apiErrorMessage(err, "Could not work out settings."));
    } finally {
      setBusy(false);
    }
  }

  async function connect() {
    if (modelId === "") return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api.quickConnect({ model_id: modelId, ...(agentId !== "" ? { agent_id: agentId } : {}) }));
      onConnected?.();
    } catch (err) {
      setResult(null);
      setError(apiErrorMessage(err, "Could not connect."));
    } finally {
      setBusy(false);
    }
  }

  const message = result ? quickConnectMessage(result) : null;
  return (
    <section className="quick-connect">
      <div className="section-heading">
        <h2>Quick connect</h2>
      </div>
      <p className="browse-meta">Choose a model you already have. The app picks settings that fit this computer, starts it, and can connect an agent to it.</p>
      <div className="settings-grid">
        <label className="settings-field">
          <span>Model</span>
          <select
            aria-label="Model"
            value={modelId}
            onChange={(e) => {
              setModelId(e.target.value === "" ? "" : Number(e.target.value));
              setPlan(null);
              setResult(null);
            }}
          >
            <option value="">— pick a model —</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <label className="settings-field">
          <span>Agent (optional)</span>
          <select aria-label="Agent (optional)" value={agentId} onChange={(e) => setAgentId(e.target.value === "" ? "" : Number(e.target.value))}>
            <option value="">— none —</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button disabled={modelId === "" || busy} onClick={() => void preview()}>
          Preview
        </button>
        <button className="primary" disabled={modelId === "" || busy} onClick={() => void connect()}>
          {busy ? "Working…" : "Use this model"}
        </button>
      </div>

      {plan && (
        <div className="quick-plan">
          <FitBadge fit={plan.fit} />
          <div className="mono">
            {Object.entries(plan.runtime.config_json).map(([k, v]) => (
              <div key={k}>
                {k} = {String(v)}
              </div>
            ))}
          </div>
          <ul>
            {plan.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </div>
      )}
      {error && <div className="error-text">{error}</div>}
      {message && (
        <div className={message.tone === "ok" ? "browse-ok" : message.tone === "warn" ? "browse-meta" : "error-text"} role="status">
          {message.text}
        </div>
      )}
      {result?.verification && !result.verification.ok && (
        <ul className="quick-plan">
          {result.verification.checks
            .filter((c) => c.status === "mismatch")
            .map((c) => (
              <li key={c.key}>
                <span className="mono">{c.key}</span>: {c.detail}
              </li>
            ))}
        </ul>
      )}
    </section>
  );
}
