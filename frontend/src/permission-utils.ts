/**
 * frontend/src/permission-utils.ts
 *
 * Pure helpers behind PermissionPrompt and PermissionsPage -- kept
 * separate from React so the merge/labeling rules are cheap to test
 * exhaustively.
 */

import type { PendingPermissionRequest, RiskLevel } from "./api";

export const RISK_LABEL: Record<RiskLevel, string> = {
  LOW: "Low risk",
  MEDIUM: "Medium risk",
  HIGH: "High risk",
  CRITICAL: "Critical risk",
};

/** Merges a fresh GET /pending snapshot with the live queue, keeping
 * queue order stable (new requests appended, not resorted) and dropping
 * anything the snapshot no longer has (already resolved elsewhere, or
 * timed out). Existing entries keep their queue position. */
export function mergePending(
  queue: PendingPermissionRequest[],
  snapshot: PendingPermissionRequest[]
): PendingPermissionRequest[] {
  const byId = new Map(snapshot.map((r) => [r.id, r]));
  const kept = queue.filter((r) => byId.has(r.id));
  const keptIds = new Set(kept.map((r) => r.id));
  const added = snapshot.filter((r) => !keptIds.has(r.id));
  return [...kept, ...added];
}

/** What the tool being asked about is, in one line, for the modal's title. */
export function scopeLabel(scopeType: PendingPermissionRequest["scope_type"], scopeKey: string): string {
  if (scopeType === "mcp_server") return `MCP server #${scopeKey}`;
  const sep = scopeKey.indexOf(":");
  if (sep === -1) return `MCP tool ${scopeKey}`;
  return `${scopeKey.slice(sep + 1)} (server #${scopeKey.slice(0, sep)})`;
}
