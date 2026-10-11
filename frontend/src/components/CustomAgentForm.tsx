import { useEffect, useState } from "react";
import { api, apiErrorMessage, type AgentSummary, type CustomAgentCheck, type CustomAgentPreset } from "../api";
import { parseSpecJson, specFromPreset } from "../connect-utils";

/**
 * Describe any agent CLI once -- where it is, where its provider settings live,
 * which values it needs -- and the app configures and launches it like a built-in
 * one. Nothing is written until "Save"; "Check" only shows what WOULD be written.
 */
export function CustomAgentForm({ agent, onSaved }: { agent: AgentSummary; onSaved: (a: AgentSummary) => void }) {
  const saved = (agent.config_json as { cli_spec?: unknown }).cli_spec;
  const [presets, setPresets] = useState<CustomAgentPreset[]>([]);
  const [text, setText] = useState(saved ? JSON.stringify(saved, null, 2) : "");
  const [check, setCheck] = useState<CustomAgentCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.customAgentPresets().then(setPresets).catch(() => undefined);
  }, []);

  function usePreset(id: string) {
    const preset = presets.find((p) => p.id === id);
    if (!preset) return;
    setText(JSON.stringify(specFromPreset(preset, agent.name, "your-command"), null, 2));
    setCheck(null);
    setError(null);
  }

  async function runCheck(): Promise<CustomAgentCheck | null> {
    const parsed = parseSpecJson(text);
    if ("error" in parsed) {
      setError(parsed.error);
      setCheck(null);
      return null;
    }
    try {
      const result = await api.checkCustomAgent(parsed.spec);
      setCheck(result);
      setError(null);
      return result;
    } catch (err) {
      setCheck(null);
      setError(apiErrorMessage(err, "The spec was not accepted."));
      return null;
    }
  }

  async function save() {
    setBusy(true);
    try {
      const result = await runCheck();
      if (!result) return;
      onSaved(await api.updateAgentConfig(agent.id, { agent_backend: "custom_cli", config_json: { cli_spec: result.spec } }));
    } catch (err) {
      setError(apiErrorMessage(err, "Could not save."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="custom-agent">
      <div className="settings-grid">
        <label className="settings-field">
          <span>Start from</span>
          <select aria-label="Start from" value="" onChange={(e) => usePreset(e.target.value)}>
            <option value="">— a template —</option>
            {presets.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="settings-field" style={{ marginTop: 8 }}>
        <span>How to run and configure it (JSON)</span>
        <textarea
          aria-label="How to run and configure it"
          className="mono custom-agent-spec"
          rows={12}
          spellCheck={false}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setCheck(null);
          }}
        />
      </label>
      <p className="browse-meta">
        Values can use <span className="mono">{"{base_url}"}</span> <span className="mono">{"{api_key}"}</span>{" "}
        <span className="mono">{"{model}"}</span> <span className="mono">{"{context}"}</span>. Commands run directly, never through a shell.
      </p>
      <div style={{ display: "flex", gap: 8 }}>
        <button disabled={busy || !text.trim()} onClick={() => void runCheck()}>
          Check
        </button>
        <button className="primary" disabled={busy || !text.trim()} onClick={() => void save()}>
          Save
        </button>
      </div>
      {error && <div className="error-text">{error}</div>}
      {check && (
        <div className="custom-agent-check" role="status">
          <div>
            {check.detected
              ? `Found at ${check.detected_path}${check.detected_version ? ` (${check.detected_version})` : ""}`
              : "Not found on this computer — install it or give its full path."}
          </div>
          {Object.keys(check.preview.settings).length > 0 && (
            <div>
              <strong>Settings it will write</strong>
              {Object.entries(check.preview.settings).map(([k, v]) => (
                <div key={k} className="mono">
                  {k} = {v}
                </div>
              ))}
            </div>
          )}
          {Object.keys(check.preview.env).length > 0 && (
            <div>
              <strong>Environment it will get</strong>
              {Object.entries(check.preview.env).map(([k, v]) => (
                <div key={k} className="mono">
                  {k}={v}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
