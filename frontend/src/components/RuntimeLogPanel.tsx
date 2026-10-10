import { useState } from "react";
import { api, type RuntimeInspect } from "../api";
import { deviceWarning, inspectProblems, placementSummary } from "../inspect-utils";

/**
 * The runtime's (llama-server's) own output, in full, selectable, with Copy
 * and Download. It's what proves what the server REALLY did at startup:
 * which backend loaded (CUDA or CPU), how many layers went to the GPU, the
 * real context size. Loaded on demand (button), not polled.
 */
export function RuntimeLogPanel({ runtimeId }: { runtimeId: number }) {
  const [lines, setLines] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [proof, setProof] = useState<RuntimeInspect | null>(null);
  const [proofError, setProofError] = useState<string | null>(null);

  async function checkProof() {
    setProofError(null);
    try {
      setProof(await api.getRuntimeInspect(runtimeId));
    } catch (e) {
      setProofError(e instanceof Error ? e.message : "Could not inspect the runtime");
    }
  }

  async function load() {
    setLoading(true);
    setError(null);
    setNote(null);
    try {
      const res = await api.getRuntimeLogs(runtimeId, 2000);
      setLines(res.lines);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the log");
    } finally {
      setLoading(false);
    }
  }

  const text = (lines ?? []).join("\n");

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setNote("Copied.");
    } catch {
      setNote("Copy blocked by the browser — select the text and press Ctrl+C.");
    }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `runtime-${runtimeId}-log.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="panel git-diff-summary" data-testid="runtime-log-panel">
      <div className="section-heading">
        <h3>Runtime log (llama-server output)</h3>
      </div>
      <div data-testid="runtime-proof" style={{ marginBottom: 12 }}>
        <button onClick={() => void checkProof()}>بررسی اینکه سرور واقعاً چه تنظیماتی گرفته</button>
        {proofError && <div className="error-text">{proofError}</div>}
        {proof && (
          <div style={{ marginTop: 8 }}>
            {(() => {
              const place = placementSummary(proof);
              const dev = deviceWarning(proof.devices);
              const problems = inspectProblems(proof);
              return (
                <>
                  {place && (
                    <div>
                      {place.icon} {place.text}
                    </div>
                  )}
                  {dev && <div className="error-text">⚠ {dev}</div>}
                  {proof.config_changed_since_start && (
                    <div className="muted">⚠ تنظیمات بعد از آخرین start عوض شده؛ تا Restart اعمال نمی‌شود.</div>
                  )}
                  {problems.map((p) => (
                    <div key={p} className="error-text">
                      • {p}
                    </div>
                  ))}
                  {proof.ok && !dev && <div className="muted">✔ همه‌ی تنظیمات بررسی‌شده با سرور یکی است.</div>}
                  {proof.launch_command && (
                    <details>
                      <summary>دستور واقعی اجرا</summary>
                      <textarea
                        readOnly
                        dir="ltr"
                        rows={3}
                        style={{ width: "100%", fontFamily: "var(--font-mono, monospace)", fontSize: 12 }}
                        value={proof.launch_command.join(" ")}
                      />
                    </details>
                  )}
                </>
              );
            })()}
          </div>
        )}
      </div>
      <button onClick={() => void load()} disabled={loading}>
        {loading ? "Loading…" : lines == null ? "Load log" : "Refresh"}
      </button>
      {lines != null && lines.length > 0 && (
        <>
          <button style={{ marginInlineStart: 8 }} onClick={() => void copy()}>
            Copy all
          </button>
          <button style={{ marginInlineStart: 8 }} onClick={download}>
            Download .txt
          </button>
        </>
      )}
      {note && <span className="muted" style={{ marginInlineStart: 8 }}>{note}</span>}
      {error && <div className="error-text">{error}</div>}
      {lines != null && lines.length === 0 && (
        <p className="muted">No output yet — start the runtime first (the log is kept in memory for the running process).</p>
      )}
      {lines != null && lines.length > 0 && (
        <textarea
          readOnly
          value={text}
          rows={18}
          dir="ltr"
          style={{ width: "100%", fontFamily: "var(--font-mono, monospace)", fontSize: 12, marginTop: 8 }}
        />
      )}
    </div>
  );
}
