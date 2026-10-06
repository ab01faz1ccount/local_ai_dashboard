/**
 * frontend/src/session-utils.ts
 *
 * Small pure helpers for SessionsPage: formatting and the file download
 * trigger. Kept out of the component so the formatting rules are testable
 * without rendering anything.
 */

import type { SessionSummary } from "./api";

export function formatDuration(startedAt: string, endedAt: string | null, now: number = Date.now()): string {
  const start = Date.parse(startedAt);
  const end = endedAt ? Date.parse(endedAt) : now;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "—";
  const totalSec = Math.floor((end - start) / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function totalTokens(s: Pick<SessionSummary, "prompt_tokens_total" | "completion_tokens_total">): number {
  return (s.prompt_tokens_total ?? 0) + (s.completion_tokens_total ?? 0);
}

/** Tool-call-related event counts out of a session's event_counts map. */
export function toolCallStats(eventCounts: Record<string, number>): { called: number; completed: number; failed: number } {
  return {
    called: eventCounts["tool.called"] ?? 0,
    completed: eventCounts["tool.completed"] ?? 0,
    failed: eventCounts["tool.failed"] ?? 0,
  };
}

const MIME: Record<string, string> = {
  json: "application/json",
  jsonl: "application/x-ndjson",
  md: "text/markdown",
};

/** Triggers a browser download of `content` as `filename`. */
export function downloadTextFile(filename: string, content: string): void {
  const ext = filename.split(".").pop() ?? "";
  const blob = new Blob([content], { type: `${MIME[ext] ?? "text/plain"};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
