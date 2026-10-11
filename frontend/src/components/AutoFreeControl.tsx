import { useEffect, useState } from "react";
import { api, apiErrorMessage, type AutoFreeMode, type AutoFreePolicy } from "../api";
import { AUTO_FREE_OPTIONS, autoFreePayload, describeAutoFree, minutesFor } from "../connect-utils";

/**
 * "Free the memory when the work is over." One runtime's auto-free policy:
 * the model leaves RAM/VRAM when the last agent closes, or after a quiet
 * period -- so a finished agent session doesn't keep the GPU busy.
 */
export function AutoFreeControl({ runtimeId }: { runtimeId: number }) {
  const [policy, setPolicy] = useState<AutoFreePolicy | null>(null);
  const [mode, setMode] = useState<AutoFreeMode>("off");
  const [minutes, setMinutes] = useState("10");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getAutoFree(runtimeId)
      .then((p) => {
        if (cancelled) return;
        setPolicy(p);
        setMode(p.mode);
        setMinutes(p.mode === "off" ? "10" : minutesFor(p));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [runtimeId]);

  const payload = autoFreePayload(mode, minutes);
  const unchanged =
    policy !== null &&
    payload !== null &&
    payload.mode === policy.mode &&
    (mode === "off" || minutesFor(policy) === minutes.trim());

  async function save() {
    if (!payload) return;
    setSaving(true);
    setError(null);
    try {
      setPolicy(await api.setAutoFree(runtimeId, payload));
    } catch (err) {
      setError(apiErrorMessage(err, "Could not save this."));
    } finally {
      setSaving(false);
    }
  }

  const hint = AUTO_FREE_OPTIONS.find((o) => o.mode === mode)?.hint;
  return (
    <div className="auto-free">
      <div className="auto-free-row">
        <label className="settings-field">
          <span>Free memory</span>
          <select aria-label="Free memory" value={mode} onChange={(e) => setMode(e.target.value as AutoFreeMode)}>
            {AUTO_FREE_OPTIONS.map((o) => (
              <option key={o.mode} value={o.mode}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        {mode !== "off" && (
          <label className="settings-field auto-free-minutes">
            <span>Minutes</span>
            <input aria-label="Minutes" inputMode="numeric" value={minutes} onChange={(e) => setMinutes(e.target.value)} aria-invalid={payload === null} />
          </label>
        )}
        <button disabled={saving || payload === null || unchanged} onClick={() => void save()}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      <div className="auto-free-hint">{hint}</div>
      {mode !== "off" && payload === null && <div className="error-text">Enter a whole number of minutes, 1 to 1440.</div>}
      {policy && <div className="auto-free-current">Now: {describeAutoFree(policy)}</div>}
      {error && <div className="error-text">{error}</div>}
    </div>
  );
}
