import type { GgufFacts, ModelComparisonRow } from "./api";

/** True when the shown values aren't all the same: the rows worth a second look in a comparison. */
export function valuesDiffer(values: (string | null | undefined)[]): boolean {
  if (values.length < 2) return false;
  const shown = values.map((v) => (v == null || v === "" ? "—" : v));
  return new Set(shown).size > 1;
}

/** "Q4_K 83% · Q6_K 17%": the quantization types a file's weights really use. */
export function tensorMixText(mix: GgufFacts["tensor_mix"] | undefined, limit = 3): string {
  if (!mix || mix.length === 0) return "—";
  const shown = mix.slice(0, limit).map((m) => {
    const pct = m.share * 100;
    return `${m.type} ${pct >= 1 ? Math.round(pct) : "<1"}%`;
  });
  return shown.join(" · ");
}

/** Context length as "32,768" (the trained limit, not what a runtime is set to). */
export function formatContext(n: number | null | undefined): string {
  return n == null ? "—" : n.toLocaleString("en-US");
}

/** Memory the attention cache takes for `tokens` of context at f16, from bytes per token. */
export function kvCacheText(cache: GgufFacts["kv_cache"] | undefined, tokens = 1000): string {
  if (!cache) return "—";
  const bytes = cache.bytes_per_token * tokens;
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(mb < 10 ? 1 : 0)} MB`;
}

export function bitsText(bpw: number | null | undefined): string {
  return bpw == null ? "—" : `${bpw.toFixed(2)} bits`;
}

export function headsText(f: Pick<GgufFacts, "attention_heads" | "kv_heads"> | null | undefined): string {
  if (!f || f.attention_heads == null) return "—";
  return f.kv_heads != null && f.kv_heads !== f.attention_heads ? `${f.attention_heads} (${f.kv_heads} for cache)` : String(f.attention_heads);
}

/**
 * When two or more compared models were looked up in the same Hugging Face repository, return the
 * repo: a model card describes the whole repository, so everything under "From the web" is the same
 * for all of them by nature. Only the file's own header can tell a Q4 from a Q6.
 */
export function sharedWebRepo(rows: ModelComparisonRow[]): string | null {
  const counts = new Map<string, number>();
  for (const r of rows) {
    const repo = r.knowledge?.profile ? r.knowledge.repo : null;
    if (repo) counts.set(repo.toLowerCase(), (counts.get(repo.toLowerCase()) ?? 0) + 1);
  }
  for (const r of rows) {
    const repo = r.knowledge?.repo;
    if (repo && (counts.get(repo.toLowerCase()) ?? 0) > 1) return repo;
  }
  return null;
}
