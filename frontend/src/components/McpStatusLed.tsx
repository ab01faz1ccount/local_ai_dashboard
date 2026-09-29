import type { McpStatusName } from "../api";

/**
 * Same square "rack light" as StatusLed, for an MCP server's connection
 * state. Reuses the runtime LED colors (styles.css `.status-led`) rather
 * than inventing a second look: CONNECTED reads like ONLINE, CONNECTING
 * like STARTING, DISCONNECTED like OFFLINE. The visible label is always
 * the real MCP state name -- color never carries the meaning alone.
 */
const LED_STATE: Record<McpStatusName, string> = {
  CONNECTED: "ONLINE",
  CONNECTING: "STARTING",
  DISCONNECTED: "OFFLINE",
  ERROR: "ERROR",
};

export function McpStatusLed({ state }: { state: McpStatusName }) {
  return (
    <span className="status-led" data-state={LED_STATE[state]} data-mcp-state={state}>
      {state}
    </span>
  );
}
