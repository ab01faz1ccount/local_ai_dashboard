import "@xterm/xterm/css/xterm.css";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { useEffect, useRef, useState } from "react";
import { buildAgentTerminalWsUrl } from "../api";

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

    socket.onopen = () => setStatus("open");
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

  return (
    <div className="agent-terminal-shell" style={{ borderColor: brandColor }}>
      <div className="agent-terminal-header" style={{ color: brandColor }}>
        <span className={`terminal-status-dot terminal-status-${status}`} />
        {status === "connecting" && "در حال اتصال…"}
        {status === "open" && "متصل"}
        {status === "closed" && "پردازش بسته شد"}
        {status === "error" && (errorMessage ?? "خطا در اتصال")}
      </div>
      <div ref={containerRef} className="agent-terminal-body" />
    </div>
  );
}
