import { describe, it, expect } from "vitest";
import { MAX_NOTIFICATIONS, addNotification, eventToNotification, type AppNotification } from "../src/notification-utils";
import type { LogEvent } from "../src/api";

const evt = (event_type: string, metadata: Record<string, unknown> = {}, event_id = "e1"): LogEvent =>
  ({ id: 1, event_id, event_type, timestamp: "2026-01-01T00:00:00Z", device_id: null, runtime_id: null, agent_id: null, session_id: null,
     source: { system: "local", project_id: null, agent_id: null, session_id: null, task_id: null }, metadata }) as unknown as LogEvent;

describe("eventToNotification", () => {
  it("runtime.crashed is an error", () => {
    const n = eventToNotification(evt("runtime.crashed", { error: "segfault" }))!;
    expect(n.severity).toBe("error");
    expect(n.detail).toBe("segfault");
  });
  it("permission.requested is a warning that names what's being asked", () => {
    const n = eventToNotification(evt("permission.requested", { description: "delete_file(/tmp/x)", scope_key: "1:delete_file" }))!;
    expect(n.severity).toBe("warning");
    expect(n.detail).toBe("delete_file(/tmp/x)");
  });
  it("permission.requested falls back to scope_key without a description", () => {
    expect(eventToNotification(evt("permission.requested", { scope_key: "1:x" }))!.detail).toBe("1:x");
  });
  it("mcp.connect_failed names the server", () => {
    expect(eventToNotification(evt("mcp.connect_failed", { name: "files", error: "boom" }))!.title).toBe("Could not connect to files");
  });
  it("mcp.disconnected is news only when the connection was LOST", () => {
    expect(eventToNotification(evt("mcp.disconnected", { reason: "requested", name: "files" }))).toBeNull();
    const lost = eventToNotification(evt("mcp.disconnected", { reason: "lost", name: "files", error: "ping failed" }))!;
    expect(lost.severity).toBe("error");
    expect(lost.title).toBe("Lost connection to files");
  });
  it("tool.failed and inference.failed surface", () => {
    expect(eventToNotification(evt("tool.failed", { tool: "1__echo", reason: "permission_denied" }))!.title).toBe("Tool 1__echo failed");
    expect(eventToNotification(evt("inference.failed", { error: "timeout" }))!.severity).toBe("error");
  });
  it.each(["runtime.started", "inference.completed", "tool.completed", "permission.approved", "mcp.connected", "runtime.metrics", "session.started"])(
    "%s is NOT notification-worthy", (t) => expect(eventToNotification(evt(t))).toBeNull());
  it("uses the event's own id so redelivery can dedupe", () => {
    expect(eventToNotification(evt("runtime.crashed", {}, "abc"))!.id).toBe("abc");
  });
  it("tolerates missing metadata", () => {
    expect(eventToNotification({ ...evt("runtime.crashed"), metadata: undefined } as unknown as LogEvent)).not.toBeNull();
  });
});

describe("addNotification", () => {
  const n = (id: string): AppNotification => ({ id, severity: "info", title: id, detail: null, timestamp: "t", eventType: "x" });
  it("prepends (newest first)", () => expect(addNotification([n("a")], n("b")).map((x) => x.id)).toEqual(["b", "a"]));
  it("drops a duplicate id and returns the SAME list (so callers can detect no-change)", () => {
    const list = [n("a")];
    expect(addNotification(list, n("a"))).toBe(list);
  });
  it("caps the list", () => {
    let list: AppNotification[] = [];
    for (let i = 0; i < MAX_NOTIFICATIONS + 10; i++) list = addNotification(list, n(String(i)));
    expect(list).toHaveLength(MAX_NOTIFICATIONS);
    expect(list[0].id).toBe(String(MAX_NOTIFICATIONS + 9)); // newest kept
  });
});
