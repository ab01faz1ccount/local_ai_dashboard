import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type RuntimeInspect } from "../api";
import { deviceWarning, inspectProblems, placementSummary } from "../inspect-utils";
import { countProblems, filterLogLines, lineLevel } from "../log-utils";

const REFRESH_MS = 2000;
const LINES = 2000; // the backend keeps this many per process

/**
 * The raw output of ONE runtime's llama-server, plus the check of what that
 * process really did (command line, applied settings, CPU vs GPU). Lives in
 * Logs -> "Server output", under the same runtime filter as the event log.
 *
 * Follows the log while "Live" is on (re-reads every couple of seconds and
 * sticks to the bottom unless the user scrolled up to read).
 */
export function RuntimeLogPanel({ runtimeId, runtimeName }: { runtimeId: number; runtimeName?: string }) {
  const [lines, setLines] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(true);
  const [query, setQuery] = useState("");
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const [proof, setProof] = useState<RuntimeInspect | null>(null);
  const [proofError, setProofError] = useState<string | null>(null);

  const boxRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true); // user is at the bottom -> keep following
  const inFlight = useRef(false);
  const seq = useRef(0); // ignore an answer for a runtime we've since left

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    const mine = seq.current;
    try {
      const res = await api.getRuntimeLogs(runtimeId, LINES);
      if (mine !== seq.current) return;
      setLines(res.lines);
      setError(null);
    } catch (e) {
      if (mine !== seq.current) return;
      setError(e instanceof Error ? e.message : "Could not load the log");
    } finally {
      inFlight.current = false;
    }
  }, [runtimeId]);

  // switching runtime: forget everything about the previous one
  useEffect(() => {
    seq.current++;
    inFlight.current = false;
    setLines(null);
    setProof(null);
    setProofError(null);
    setNote(null);
    stickRef.current = true;
    void load();
  }, [runtimeId, load]);

  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [live, load]);

  const visible = useMemo(() => filterLogLines(lines ?? [], query, onlyProblems), [lines, query, onlyProblems]);
  const problems = useMemo(() => countProblems(lines ?? []), [lines]);

  // follow the bottom
  useEffect(() => {
    const box = boxRef.current;
    if (box && stickRef.current) box.scrollTop = box.scrollHeight;
  }, [visible]);

  function onScroll() {
    const box = boxRef.current;
    if (!box) return;
    stickRef.current = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
  }

  async function checkProof() {
    setProofError(null);
    try {
      setProof(await api.getRuntimeInspect(runtimeId));
    } catch (e) {
      setProofError(e instanceof Error ? e.message : "Could not inspect the runtime");
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(visible.join("\n"));
      setNote(`Copied ${visible.length} lines.`);
    } catch {
      setNote("Copy blocked by the browser — select the text and press Ctrl+C.");
    }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([(lines ?? []).join("\n")], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(runtimeName ?? `runtime-${runtimeId}`).replace(/[^\w.-]+/g, "_")}-server-log.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const place = placementSummary(proof);
  const deviceNote = deviceWarning(proof?.devices);
  const proofProblems = inspectProblems(proof);

  return (
    <div data-testid="runtime-log-panel">
      <div className="panel server-proof" data-testid="runtime-proof">
        <div className="section-heading">
          <h3>What the server really did</h3>
          <button onClick={() => void checkProof()}>Check now</button>
        </div>
        {!proof && !proofError && (
          <p className="muted">Compares the command line, the settings the server reports, and where the model runs (CPU or GPU).</p>
        )}
        {proofError && <div className="error-text">{proofError}</div>}
        {proof && (
          <>
            {place && (
              <div className={`proof-line proof-${place.tone}`}>
                <span aria-hidden="true">{place.icon}</span> {place.text}
              </div>
            )}
            {deviceNote && <div className="proof-line proof-warn">⚠ {deviceNote}</div>}
            {proof.config_changed_since_start && (
              <div className="proof-line proof-wait">⚠ Settings changed after the last start — they apply after a restart.</div>
            )}
            {proofProblems.map((p) => (
              <div key={p} className="proof-line proof-warn">
                • {p}
              </div>
            ))}
            {proof.ok && !deviceNote && <div className="proof-line proof-ok">✔ Every checked setting matches the server.</div>}
            {proof.launch_command && (
              <details>
                <summary>Launch command</summary>
                <textarea readOnly dir="ltr" rows={3} className="launch-command" value={proof.launch_command.join(" ")} />
              </details>
            )}
          </>
        )}
      </div>

      <div className="panel server-log">
        <div className="server-log-toolbar">
          <input
            type="search"
            aria-label="Filter log lines"
            placeholder="Filter lines (all words must match)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <label className="check-inline">
            <input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />
            Warnings and errors only
          </label>
          <label className="check-inline">
            <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
            Live
          </label>
          <button onClick={() => void load()}>Refresh</button>
          <button onClick={() => void copy()} disabled={visible.length === 0}>
            Copy shown
          </button>
          <button onClick={download} disabled={!lines || lines.length === 0}>
            Download all
          </button>
        </div>

        <div className="muted server-log-meta">
          {lines == null
            ? "Loading…"
            : `${visible.length} of ${lines.length} lines` +
              (problems.error || problems.warn ? ` · ${problems.error} errors · ${problems.warn} warnings` : "")}
          {note && <span> · {note}</span>}
        </div>
        {error && <div className="error-text">{error}</div>}

        {lines != null && lines.length === 0 ? (
          <p className="muted">
            No output yet. The log is kept for a runtime this app started — start it from the Dashboard (a server started
            outside the app can't be read here).
          </p>
        ) : (
          <div ref={boxRef} className="log-box" onScroll={onScroll} tabIndex={0} aria-label="Server output" dir="ltr">
            {visible.map((line, i) => (
              <div key={i} className={`log-line log-${lineLevel(line)}`}>
                {line}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
