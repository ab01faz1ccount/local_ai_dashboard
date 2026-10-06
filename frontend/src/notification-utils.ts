/**
 * frontend/src/notification-utils.ts
 *
 * Which events are worth interrupting someone for, and how to phrase
 * them -- the policy behind NotificationsPanel (master build prompt
 * section 24), separated from the component so it can be tested and
 * tuned without rendering. Deliberately a short allow-list: the event
 * log (LogsPage) already has everything; a notification is only for
 * "something needs your attention or just went wrong".
 */

import type { LogEvent } from "./api";

export type NotificationSeverity = "error" | "warning" | "info";

export interface AppNotification {
  /** The event's own event_id -- stable, so the same event delivered twice dedups. */
  id: string;
  severity: NotificationSeverity;
  title: string;
  detail: string | null;
  timestamp: string;
  eventType: string;
}

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

/** Returns a notification for an event worth surfacing, or null. */
export function eventToNotification(ev: LogEvent): AppNotification | null {
  const m = ev.metadata ?? {};
  const base = { id: ev.event_id, timestamp: ev.timestamp, eventType: ev.event_type };

  switch (ev.event_type) {
    case "runtime.crashed":
      return { ...base, severity: "error", title: "A runtime crashed", detail: str(m.error) ?? str(m.reason) };
    case "permission.requested":
      return {
        ...base,
        severity: "warning",
        title: "A tool is waiting for your permission",
        detail: str(m.description) ?? str(m.scope_key),
      };
    case "mcp.connect_failed":
      return {
        ...base,
        severity: "error",
        title: `Could not connect to ${str(m.name) ?? "an MCP server"}`,
        detail: str(m.error),
      };
    case "mcp.disconnected":
      // A requested disconnect is the user's own action -- only a lost connection is news.
      if (m.reason !== "lost") return null;
      return { ...base, severity: "error", title: `Lost connection to ${str(m.name) ?? "an MCP server"}`, detail: str(m.error) };
    case "tool.failed":
      return {
        ...base,
        severity: "warning",
        title: `Tool ${str(m.tool) ?? ""} failed`.replace("  ", " "),
        detail: str(m.reason) ?? str(m.error),
      };
    case "inference.failed":
      return { ...base, severity: "error", title: "A model request failed", detail: str(m.error) };
    default:
      return null;
  }
}

export const MAX_NOTIFICATIONS = 50;

/** Prepends a new notification, newest first, dropping a duplicate id and
 * capping the list so a flapping server can't grow it without bound. */
export function addNotification(list: AppNotification[], n: AppNotification, max = MAX_NOTIFICATIONS): AppNotification[] {
  if (list.some((x) => x.id === n.id)) return list;
  return [n, ...list].slice(0, max);
}
