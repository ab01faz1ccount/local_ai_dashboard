// Pure helpers for the "send MCP servers to the agent's own config" panel.
import type { AgentMcpReport, AgentMcpServerState } from "./api";

export type StateTone = "ok" | "warn" | "muted";

const LABELS: Record<string, { text: string; tone: StateTone }> = {
  in_sync: { text: "in its config", tone: "ok" },
  missing: { text: "not written yet", tone: "warn" },
  differs: { text: "differs from the app", tone: "warn" },
  needs_confirm: { text: "has secrets — needs your OK", tone: "warn" },
  unsupported: { text: "transport not supported here", tone: "muted" },
};

export function stateLabel(status: string): { text: string; tone: StateTone } {
  return LABELS[status] ?? { text: status, tone: "muted" };
}

export function needsConfirm(report: AgentMcpReport): boolean {
  return report.servers.some((s) => s.status === "needs_confirm");
}

/** Servers that a sync would write or change. */
export function pendingCount(report: AgentMcpReport): number {
  return report.servers.filter((s) => s.status === "missing" || s.status === "differs").length + report.stale.length;
}

export function summaryText(report: AgentMcpReport): string {
  const total = report.servers.length;
  const inSync = report.servers.filter((s) => s.status === "in_sync").length;
  const parts = [`${inSync} of ${total} in ${report.agent_name}'s own config`];
  if (report.stale.length > 0) {
    parts.push(`${report.stale.length} left over from servers you detached`);
  }
  return parts.join("; ");
}

export function detailText(s: AgentMcpServerState): string {
  if (s.status === "differs" && s.fields.length > 0) return `differs in: ${s.fields.join(", ")}`;
  return s.detail;
}

/** What a probe proves — the wording must not claim more than the command did. */
export function probeText(p: { ok: boolean; live: boolean }): string {
  if (p.ok && p.live) return "The agent's own CLI connected to it.";
  if (p.ok) return "The agent's CLI can see the entry. This does not prove it connects.";
  return p.live ? "The agent's CLI could not connect." : "The agent's CLI could not read the entry.";
}
