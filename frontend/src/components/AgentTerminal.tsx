import "@xterm/xterm/css/xterm.css";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { useEffect, useRef, useState } from "react";
import { api, buildAgentTerminalWsUrl } from "../api";
import type { AgentPairing, ConfigCheckReport } from "../api";
import { configProblems, configSummary, pairingSummary } from "../pairing-utils";
import type { Tone } from "../pairing-utils";

const PAIRING_POLL_MS = 2000;
const TONE_COLOR: Record<Tone, string> = { ok: "#3fb950", warn: "#f0883e", wait: "#8b949e" };

/**
 * A real terminal, not a chat-bubble approximation of one: this attaches
 * to the actual agent CLI process (see backend/api/ws.py's
 * /ws/agent-terminal route + core/agents/pty_session.py), so whatever
 * that process prints -- its own banner, its own colors, interactive
 * prompts -- shows up exactly as it would in a real terminal window.
 * `brandColor` only themes this panel's border/header; it never touches
 * what the terminal itself renders.
 */
export function AgentTerminal({
  agentId,
  runtimeId,
  brandColor,
}: {
  agentId: number;
  runtimeId: number;
  brandColor: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const [status, setStatus] = useState<"connecting" | "open" | "closed" | "error">("connecting");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // Proof that this agent is REALLY wired to this runtime (not just labeled):
  // the agent's own settings read back, and the runtime's server log showing
  // the agent's requests actually arriving.
  const [configReport, setConfigReport] = useState<ConfigCheckReport | null>(null);
  const [pairing, setPairing] = useState<AgentPairing | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const p = await api.getAgentPairing(agentId, runtimeId);
        if (!cancelled) setPairing(p);
      } catch {
        // transient (backend restarting, etc.) -- the next tick retries
      }
    };
    const timer = window.setInterval(poll, PAIRING_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [agentId, runtimeId]);

  useEffect(() => {
    if (!containerRef.current) return;

    const term = new Terminal({
      convertEol: true,
      fontFamily: "var(--font-mono, monospace)",
      fontSize: 13,
      theme: { background: "#00000000" }, // transparent, let the panel's own background show through
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(containerRef.current);
    fitAddon.fit();
    termRef.current = term;

    const socket = new WebSocket(buildAgentTerminalWsUrl(agentId, runtimeId, term.cols, term.rows));
    socketRef.current = socket;

    socket.onopen = () => {
      setStatus("open");
      term.focus(); // type immediately, no need to click the terminal first
    };
    socket.onclose = () => setStatus((prev) => (prev === "error" ? prev : "closed"));
    socket.onerror = () => setStatus("error");
    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "output") {
          term.write(msg.data);
        } else if (msg.type === "error") {
          setStatus("error");
          setErrorMessage(msg.message);
        } else if (msg.type === "exit") {
          setStatus("closed");
        } else if (msg.type === "config_check") {
          setConfigReport(msg.report as ConfigCheckReport);
        }
      } catch {
        // malformed frame -- ignore, next one arrives shortly
      }
    };

    const inputDisposable = term.onData((data) => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "input", data }));
      }
    });

    const resizeObserver = new ResizeObserver(() => {
      // hidden (another page is showing) -> 0x0; fitting now would shrink the
      // PTY to a few columns and garble the session
      if (!containerRef.current || containerRef.current.clientWidth === 0) return;
      fitAddon.fit();
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      }
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      inputDisposable.dispose();
      resizeObserver.disconnect();
      socket.close();
      term.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, runtimeId]);

  const configLine = configSummary(configReport);
  const problems = configProblems(configReport);
  const pairingLine = pairingSummary(pairing);

  return (
    <div className="agent-terminal-shell" style={{ borderColor: brandColor }}>
      <div className="agent-terminal-header" style={{ color: brandColor }}>
        <span className={`terminal-status-dot terminal-status-${status}`} />
        {status === "connecting" && "در حال اتصال…"}
        {status === "open" && "متصل"}
        {status === "closed" && "پردازش بسته شد"}
        {status === "error" && (errorMessage ?? "خطا در اتصال")}
      </div>
      {(configLine || pairingLine) && (
        <div
          className="agent-terminal-proof"
          data-testid="agent-terminal-proof"
          style={{ fontSize: 12, padding: "4px 12px", display: "flex", flexDirection: "column", gap: 2 }}
        >
          {[configLine, pairingLine].map(
            (line) =>
              line && (
                <div key={line.text} style={{ color: TONE_COLOR[line.tone] }}>
                  <span aria-hidden="true">{line.icon}</span> {line.text}
                </div>
              )
          )}
          {problems.map((p) => (
            <div key={p} className="mono" style={{ color: TONE_COLOR.warn }}>
              {p}
            </div>
          ))}
        </div>
      )}
      <div ref={containerRef} className="agent-terminal-body" onClick={() => termRef.current?.focus()} />
    </div>
  );
}
