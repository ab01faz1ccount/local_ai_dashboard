/**
 * frontend/src/knowledge-utils.ts
 *
 * Presentation rules for sourced facts (backend/core/knowledge.py): how a
 * fact's origin is worded, which rows a comparison shows, and which links are
 * safe to render. Pure.
 */

import type { BenchmarkResult, FactProvenance, KnowledgeFact, KnowledgeProfile } from "./api";

export function provenanceLabel(p: FactProvenance): string | null {
  switch (p) {
    case "base_model":
      return "from the base model";
    case "derived":
      return "guessed from the name";
    case "missing":
      return "not published";
    default:
      return null;
  }
}

export function isBenchmarkList(v: KnowledgeFact["value"]): v is BenchmarkResult[] {
  return Array.isArray(v) && v.length > 0 && typeof v[0] === "object" && v[0] !== null;
}

/** One line per entry for a list-valued fact; plain text otherwise; "" for a missing value. */
export function factLines(fact: KnowledgeFact): string[] {
  const v = fact.value;
  if (v == null) return [];
  if (isBenchmarkList(v)) return v.map((b) => `${b.dataset} · ${b.metric}: ${b.value}`);
  if (Array.isArray(v)) return (v as string[]).map(String);
  return [String(v)];
}

/** Rows for a table that compares several profiles: every fact key any profile
 * has, in the order of the first profile that has it, so rows line up. */
export function factKeysInOrder(profiles: (KnowledgeProfile | null | undefined)[]): { key: string; label: string }[] {
  const seen = new Map<string, string>();
  for (const p of profiles) {
    if (!p) continue;
    for (const f of p.facts) if (!seen.has(f.key)) seen.set(f.key, f.label);
  }
  return [...seen].map(([key, label]) => ({ key, label }));
}

export function findFact(profile: KnowledgeProfile | null | undefined, key: string): KnowledgeFact | null {
  return profile?.facts.find((f) => f.key === key) ?? null;
}

const LINK_HOSTS = ["huggingface.co", "github.com"];

/** Source links come from a server that read them off third-party sites: only
 * https links to the two sites the app looks things up on are rendered. */
export function safeSourceUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    if (u.protocol !== "https:") return null;
    const host = u.hostname.toLowerCase();
    return LINK_HOSTS.some((h) => host === h || host.endsWith("." + h)) ? u.toString() : null;
  } catch {
    return null;
  }
}
