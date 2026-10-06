/**
 * frontend/src/context-utils.ts
 *
 * Pure helpers behind FitBadge and ContextInspectorPanel: labels, tones
 * and the stacked-bar geometry. Separate from the components so the
 * rules (what counts as a warning, how segments are sized when the
 * window is overfull) are testable without rendering.
 */

import type { ContextCategory, ContextStatus, FitLabel } from "./api";

export type Tone = "ok" | "warn" | "bad" | "muted";

export const FIT_TEXT: Record<FitLabel, string> = {
  GOOD: "Good fit",
  WARNING: "Tight fit",
  LIKELY_TO_EXCEED: "Likely to exceed",
  NOT_RECOMMENDED: "Not recommended",
  UNKNOWN: "Unknown",
};

export const FIT_TONE: Record<FitLabel, Tone> = {
  GOOD: "ok",
  WARNING: "warn",
  LIKELY_TO_EXCEED: "bad",
  NOT_RECOMMENDED: "bad",
  UNKNOWN: "muted",
};

export const CONTEXT_STATUS_TONE: Record<ContextStatus, Tone> = {
  OK: "ok",
  HIGH: "warn",
  CRITICAL: "bad",
  OVER: "bad",
  UNKNOWN: "muted",
};

export const CONTEXT_STATUS_TEXT: Record<ContextStatus, string> = {
  OK: "Plenty of room",
  HIGH: "Getting full",
  CRITICAL: "Nearly full",
  OVER: "Over the limit",
  UNKNOWN: "Window size unknown",
};

export interface BarSegment {
  key: ContextCategory["key"];
  label: string;
  tokens: number;
  /** Width as a percentage of the BAR (not of the window). */
  widthPercent: number;
}

/** Sizes each category's slice of the bar. With a known window the bar IS
 * the window: segments are fractions of it, and whatever is left is empty
 * (headroom). When the content is bigger than the window, the bar is
 * normalised to the content instead, so every segment still shows and
 * nothing silently overflows its container. Without a window the bar shows
 * only relative proportions. Zero-token categories get no segment. */
export function buildSegments(categories: ContextCategory[], window: number | null): BarSegment[] {
  const total = categories.reduce((sum, c) => sum + c.tokens, 0);
  if (total === 0) return [];
  const denom = window && window > total ? window : total;
  return categories
    .filter((c) => c.tokens > 0)
    .map((c) => ({ key: c.key, label: c.label, tokens: c.tokens, widthPercent: (c.tokens / denom) * 100 }));
}

export function formatTokens(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("en-US");
}

/** Gap between our figure and what the server counted for the previous
 * request, when both exist -- the template's per-message framing tokens
 * live in this gap, so a big one is worth explaining rather than hiding. */
export function lastPromptNote(total: number, lastPrompt: number | null): string | null {
  if (lastPrompt == null) return null;
  const diff = lastPrompt - total;
  if (diff === 0) return "matches the previous request";
  const sign = diff > 0 ? "+" : "−";
  return `previous request used ${formatTokens(lastPrompt)} (${sign}${formatTokens(Math.abs(diff))} vs. this estimate)`;
}

export const WINDOW_SOURCE_TEXT: Record<"runtime_config" | "model_metadata", string> = {
  runtime_config: "the runtime's configured context size",
  model_metadata: "the model's trained context length (the runtime sets no explicit size)",
};
