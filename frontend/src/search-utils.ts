import type { HfFileCandidate, HfModelResult, SearchPurpose } from "./api";

/** Pure helpers behind the online model search: filters, sorting, grouping. */

export type SizeBucket = "any" | "tiny" | "small" | "medium" | "large" | "huge";
export type SortKey = "best" | "downloads" | "newest";

export interface SearchFilters {
  trustedOnly: boolean;
  hideGated: boolean;
  size: SizeBucket;
}

export const DEFAULT_FILTERS: SearchFilters = { trustedOnly: false, hideGated: false, size: "any" };

export const SIZE_BUCKETS: { id: SizeBucket; label: string; min: number; max: number }[] = [
  { id: "any", label: "Any size", min: 0, max: Infinity },
  { id: "tiny", label: "Up to 4B", min: 0, max: 4 },
  { id: "small", label: "5–9B", min: 4.0001, max: 9.5 },
  { id: "medium", label: "10–14B", min: 9.5001, max: 14.5 },
  { id: "large", label: "15–35B", min: 14.5001, max: 35.5 },
  { id: "huge", label: "36B and up", min: 35.5001, max: Infinity },
];

export const PURPOSES: { id: SearchPurpose; label: string; hint: string }[] = [
  { id: "any", label: "Anything", hint: "No preference" },
  { id: "chat", label: "Chat", hint: "Instruction-tuned for conversation" },
  { id: "coding", label: "Coding", hint: "Names that mention code or coder" },
  { id: "vision", label: "Vision", hint: "Can read images (VL, vision)" },
  { id: "reasoning", label: "Reasoning", hint: "Thinking / reasoning models" },
];

export const SORTS: { id: SortKey; label: string }[] = [
  { id: "best", label: "Best match" },
  { id: "downloads", label: "Most downloaded" },
  { id: "newest", label: "Recently updated" },
];

export function inSizeBucket(paramsB: number | null | undefined, bucket: SizeBucket): boolean {
  if (bucket === "any") return true;
  // A name that doesn't state its size is never hidden by a size filter: unknown is not "wrong".
  if (paramsB == null) return true;
  const b = SIZE_BUCKETS.find((x) => x.id === bucket)!;
  return paramsB >= b.min && paramsB <= b.max;
}

export function applyFilters(results: HfModelResult[], f: SearchFilters): HfModelResult[] {
  return results.filter((r) => {
    if (f.hideGated && r.gated) return false;
    if (f.trustedOnly && (r.trust?.tier ?? "unknown") === "unknown") return false;
    return inSizeBucket(r.params_b, f.size);
  });
}

export function sortResults(results: HfModelResult[], key: SortKey): HfModelResult[] {
  const copy = [...results];
  if (key === "best") return copy; // the backend order IS the ranking
  if (key === "downloads") return copy.sort((a, b) => b.downloads - a.downloads);
  const t = (r: HfModelResult) => (r.updated ? Date.parse(r.updated) || 0 : 0);
  return copy.sort((a, b) => t(b) - t(a));
}

/** The model itself (conversions) first, modified versions kept apart. A
 * result with no role (older payload) counts as the model itself. */
export function splitByRole(results: HfModelResult[]): { main: HfModelResult[]; variants: HfModelResult[] } {
  return {
    main: results.filter((r) => r.role !== "variant"),
    variants: results.filter((r) => r.role === "variant"),
  };
}

export function trustBadge(r: HfModelResult): { text: string; tone: "ok" | "info" } | null {
  switch (r.trust?.tier) {
    case "creator":
      return { text: "Official", tone: "ok" };
    case "quantizer":
      return { text: "Known publisher", tone: "info" };
    default:
      return null; // an unknown publisher gets no label at all, neither good nor bad
  }
}

export function followersText(n: number | null | undefined): string | null {
  if (n == null) return null;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M followers`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, "")}k followers`;
  return `${n} follower${n === 1 ? "" : "s"}`;
}

/** Does a file's quantization satisfy what the person typed? "Q4" accepts any Q4_*, "Q6_K_M" only that. */
export function quantMatches(fileQuant: string | null | undefined, wanted: string | null | undefined): boolean {
  if (!fileQuant || !wanted) return false;
  const f = fileQuant.toUpperCase();
  const w = wanted.toUpperCase();
  if (f === w) return true;
  return /^Q\d$/.test(w) && f.startsWith(`${w}_`);
}

/** Files in the order to show: ones matching the typed quantization first, then the
 * caller's own order (the recommended-for-this-PC file leads). Stable. */
export function filesForQuery(files: HfFileCandidate[], wantedQuant: string | null | undefined): HfFileCandidate[] {
  if (!wantedQuant) return files;
  const hit = files.filter((f) => quantMatches(f.quantization, wantedQuant));
  const rest = files.filter((f) => !quantMatches(f.quantization, wantedQuant));
  return [...hit, ...rest];
}

export function activeFilterCount(f: SearchFilters): number {
  return Number(f.trustedOnly) + Number(f.hideGated) + Number(f.size !== "any");
}

export function huggingFaceSearchUrl(text: string): string {
  return `https://huggingface.co/models?library=gguf&sort=downloads&search=${encodeURIComponent(text)}`;
}
