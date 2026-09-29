import { describe, it, expect } from "vitest";
import { mergePending, scopeLabel } from "../src/permission-utils";
import type { PendingPermissionRequest } from "../src/api";

const req = (o: Partial<PendingPermissionRequest> = {}): PendingPermissionRequest => ({
  id: "a", scope_type: "mcp_tool", scope_key: "1:read", risk_level: "LOW", session_id: null, agent_id: null,
  description: null, requested_at: "2026-01-01T00:00:00Z", ...o,
});

describe("mergePending", () => {
  it("keeps existing queue order and appends new entries", () => {
    const queue = [req({ id: "a" }), req({ id: "b" })];
    const snapshot = [req({ id: "b" }), req({ id: "a" }), req({ id: "c" })];
    expect(mergePending(queue, snapshot).map((r) => r.id)).toEqual(["a", "b", "c"]);
  });
  it("drops entries no longer in the snapshot (resolved elsewhere / timed out)", () => {
    const queue = [req({ id: "a" }), req({ id: "b" })];
    expect(mergePending(queue, [req({ id: "b" })]).map((r) => r.id)).toEqual(["b"]);
  });
  it("empty snapshot clears the queue", () => {
    expect(mergePending([req({ id: "a" })], [])).toEqual([]);
  });
});

describe("scopeLabel", () => {
  it("mcp_server", () => expect(scopeLabel("mcp_server", "7")).toBe("MCP server #7"));
  it("mcp_tool splits server id and tool name", () => expect(scopeLabel("mcp_tool", "7:delete_file")).toBe("delete_file (server #7)"));
  it("mcp_tool without a colon falls back gracefully", () => expect(scopeLabel("mcp_tool", "weird")).toBe("MCP tool weird"));
});
