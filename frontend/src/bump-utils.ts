/**
 * frontend/src/bump-utils.ts
 *
 * Pure helpers behind the rank-over-time ("bump") chart: bucketing measured
 * values by time, ranking the entries inside every bucket, and laying the
 * result out as SVG paths. No React/DOM in here so every edge (gaps, ties,
 * a single bucket, more entries than colors) is cheap to test.
 *
 * A rank is only ever computed from something measured. An entry with no
 * data in a bucket has NO rank there (the line breaks) -- it is never given
 * a made-up last place.
 */

import { buildSeries, type ChartPoint } from "./analytics-utils";
import type { MetricsSnapshotItem, RuntimeSummary, UsageHistoryEntry, AgentSummary } from "./api";

export type RankDirection = "higher" | "lower";

export interface Bucket {
  start: number;
  end: number;
}

export interface BumpEntry {
  id: string;
  label: string;
  /** One value per bucket; null = nothing measured in that bucket. */
  values: (number | null)[];
}

export interface BumpSeries extends BumpEntry {
  /** 1 = best. Null where `values` is null. Ranked among the SHOWN entries only. */
  ranks: (number | null)[];
  /** Color slot 0..MAX_SERIES-1: follows the entity, never its rank. */
  slot: number;
}

export const MAX_SERIES = 8;

/** `count` equal buckets covering [start, end). */
export function makeBuckets(start: number, end: number, count: number): Bucket[] {
  const n = Math.max(1, Math.floor(count));
  const span = Math.max(end - start, 1);
  const out: Bucket[] = [];
  for (let i = 0; i < n; i++) out.push({ start: start + (span * i) / n, end: start + (span * (i + 1)) / n });
  return out;
}

function bucketIndex(t: number, buckets: Bucket[]): number {
  const first = buckets[0];
  const last = buckets[buckets.length - 1];
  if (!first || t < first.start || t > last.end) return -1;
  if (t === last.end) return buckets.length - 1; // the right edge belongs to the last bucket
  const i = buckets.findIndex((b) => t >= b.start && t < b.end);
  return i;
}

/** Mean of the points that fall in each bucket; null for an empty bucket. */
export function bucketAverages(points: ChartPoint[], buckets: Bucket[]): (number | null)[] {
  const sums = buckets.map(() => 0);
  const counts = buckets.map(() => 0);
  for (const p of points) {
    const i = bucketIndex(p.t, buckets);
    if (i >= 0 && Number.isFinite(p.v)) {
      sums[i] += p.v;
      counts[i] += 1;
    }
  }
  return sums.map((s, i) => (counts[i] ? s / counts[i] : null));
}

/** Competition ranking (1,1,3): equal values share the better rank. */
export function rankValues(values: (number | null)[], direction: RankDirection): (number | null)[] {
  return values.map((v) => {
    if (v == null) return null;
    let better = 0;
    for (const o of values) {
      if (o == null) continue;
      if (direction === "higher" ? o > v : o < v) better += 1;
    }
    return better + 1;
  });
}

function rankColumn(entries: BumpEntry[], bucket: number, direction: RankDirection): (number | null)[] {
  return rankValues(entries.map((e) => e.values[bucket] ?? null), direction);
}

function meanRank(ranks: (number | null)[]): number {
  const have = ranks.filter((r): r is number => r != null);
  return have.length ? have.reduce((a, b) => a + b, 0) / have.length : Infinity;
}

export interface BumpModel {
  series: BumpSeries[];
  /** How many entries existed before the cap. */
  total: number;
  hidden: number;
  /** Highest rank used on the shown entries (the chart's row count). */
  maxRank: number;
}

/**
 * Ranks every entry per bucket, keeps the MAX_SERIES that did best overall
 * (those with the most data first, then the best mean rank), and re-ranks
 * the kept ones among themselves so the chart has no empty rows.
 * Colors are assigned by sorted id over ALL entries, so hiding or showing
 * another entry never repaints the ones that stay.
 */
export function buildBump(entries: BumpEntry[], direction: RankDirection, maxSeries: number = MAX_SERIES): BumpModel {
  const bucketCount = entries.reduce((m, e) => Math.max(m, e.values.length), 0);
  const slotOf = new Map([...entries].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)).map((e, i) => [e.id, i % MAX_SERIES]));

  const columns = Array.from({ length: bucketCount }, (_, b) => rankColumn(entries, b, direction));
  const scored = entries.map((e, idx) => {
    const own = columns.map((c) => c[idx]);
    return { e, have: own.filter((r) => r != null).length, mean: meanRank(own) };
  });
  const kept = scored
    .filter((s) => s.have > 0)
    .sort((a, b) => b.have - a.have || a.mean - b.mean || (a.e.id < b.e.id ? -1 : 1))
    .slice(0, maxSeries)
    .map((s) => s.e);

  const shownColumns = Array.from({ length: bucketCount }, (_, b) => rankColumn(kept, b, direction));
  const series: BumpSeries[] = kept.map((e, i) => ({
    ...e,
    ranks: shownColumns.map((c) => c[i]),
    slot: slotOf.get(e.id) ?? 0,
  }));
  const withData = entries.filter((e) => e.values.some((v) => v != null)).length;
  const maxRank = series.reduce((m, s) => s.ranks.reduce<number>((mm, r) => (r != null && r > mm ? r : mm), m), 0);
  return { series, total: withData, hidden: Math.max(withData - series.length, 0), maxRank };
}

// ---- layout ---------------------------------------------------------------

export interface Padding {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export interface BumpPoint {
  bucket: number;
  x: number;
  y: number;
  rank: number;
  value: number;
}

export interface BumpLayoutSeries {
  id: string;
  /** One SVG path per unbroken run of 2+ buckets. */
  paths: string[];
  points: BumpPoint[];
}

export interface BumpLayout {
  series: BumpLayoutSeries[];
  xs: number[];
  rowYs: number[];
}

const r1 = (n: number) => Math.round(n * 10) / 10;

export function bumpLayout(series: BumpSeries[], bucketCount: number, maxRank: number, width: number, height: number, pad: Padding): BumpLayout {
  const innerW = Math.max(width - pad.left - pad.right, 1);
  const innerH = Math.max(height - pad.top - pad.bottom, 1);
  const xAt = (i: number) => (bucketCount <= 1 ? pad.left + innerW / 2 : pad.left + (innerW * i) / (bucketCount - 1));
  const yAt = (rank: number) => (maxRank <= 1 ? pad.top + innerH / 2 : pad.top + (innerH * (rank - 1)) / (maxRank - 1));

  const laid = series.map((s) => {
    const points: BumpPoint[] = [];
    s.ranks.forEach((rank, bucket) => {
      const value = s.values[bucket];
      if (rank != null && value != null) points.push({ bucket, x: r1(xAt(bucket)), y: r1(yAt(rank)), rank, value });
    });
    const paths: string[] = [];
    let run: BumpPoint[] = [];
    const flush = () => {
      if (run.length >= 2) {
        let d = `M ${run[0].x} ${run[0].y}`;
        for (let i = 1; i < run.length; i++) {
          const a = run[i - 1];
          const b = run[i];
          const mid = r1((a.x + b.x) / 2);
          d += ` C ${mid} ${a.y}, ${mid} ${b.y}, ${b.x} ${b.y}`;
        }
        paths.push(d);
      }
      run = [];
    };
    for (const p of points) {
      if (run.length && p.bucket !== run[run.length - 1].bucket + 1) flush();
      run.push(p);
    }
    flush();
    return { id: s.id, paths, points };
  });
  return {
    series: laid,
    xs: Array.from({ length: bucketCount }, (_, i) => r1(xAt(i))),
    rowYs: Array.from({ length: Math.max(maxRank, 0) }, (_, i) => r1(yAt(i + 1))),
  };
}

// ---- data builders ----------------------------------------------------------

export type RuntimeMetric = "tokens_per_sec" | "last_latency_ms";
export type AgentMetric = "requests" | "avg_tokens_per_sec" | "avg_latency_ms";

export const RUNTIME_METRICS: { key: RuntimeMetric; label: string; direction: RankDirection; unit: string; digits: number }[] = [
  { key: "tokens_per_sec", label: "Speed (tokens/sec)", direction: "higher", unit: " t/s", digits: 1 },
  { key: "last_latency_ms", label: "Latency (ms)", direction: "lower", unit: " ms", digits: 0 },
];

export const AGENT_METRICS: { key: AgentMetric; label: string; direction: RankDirection; unit: string; digits: number }[] = [
  { key: "requests", label: "Requests", direction: "higher", unit: "", digits: 0 },
  { key: "avg_tokens_per_sec", label: "Speed (tokens/sec)", direction: "higher", unit: " t/s", digits: 1 },
  { key: "avg_latency_ms", label: "Latency (ms)", direction: "lower", unit: " ms", digits: 0 },
];

/** Distinct labels: two runtimes with the same name get their id appended. */
export function uniqueLabels<T extends { id: number }>(items: T[], labelOf: (item: T) => string): Map<number, string> {
  const counts = new Map<string, number>();
  for (const it of items) counts.set(labelOf(it), (counts.get(labelOf(it)) ?? 0) + 1);
  return new Map(items.map((it) => [it.id, (counts.get(labelOf(it)) ?? 0) > 1 ? `${labelOf(it)} #${it.id}` : labelOf(it)]));
}

export function runtimeEntries(
  runtimes: RuntimeSummary[],
  snapshotsByRuntime: Record<number, MetricsSnapshotItem[]>,
  metric: RuntimeMetric,
  buckets: Bucket[]
): BumpEntry[] {
  const labels = uniqueLabels(runtimes, (r) => r.model_name ?? r.name);
  return runtimes.map((r) => ({
    id: `runtime:${r.id}`,
    label: labels.get(r.id) ?? r.name,
    values: bucketAverages(buildSeries(snapshotsByRuntime[r.id] ?? [], metric), buckets),
  }));
}

/** Requests are summed; speed and latency are averaged weighted by each
 * session's own request count (a one-request session shouldn't outweigh a
 * hundred-request one). A session counts in the bucket where it STARTED. */
export function agentEntries(
  agents: AgentSummary[],
  sessions: UsageHistoryEntry[],
  metric: AgentMetric,
  buckets: Bucket[]
): BumpEntry[] {
  const labels = uniqueLabels(agents, (a) => a.name);
  return agents.map((a) => {
    const sums = buckets.map(() => 0);
    const weights = buckets.map(() => 0);
    const seen = buckets.map(() => false);
    for (const s of sessions) {
      if (s.agent_id !== a.id) continue;
      const t = Date.parse(s.started_at);
      const i = Number.isFinite(t) ? bucketIndex(t, buckets) : -1;
      if (i < 0) continue;
      if (metric === "requests") {
        sums[i] += s.requests_count;
        seen[i] = true;
        continue;
      }
      const v = metric === "avg_tokens_per_sec" ? s.avg_tokens_per_sec : s.avg_latency_ms;
      if (v == null || !Number.isFinite(v) || s.requests_count <= 0) continue;
      sums[i] += v * s.requests_count;
      weights[i] += s.requests_count;
      seen[i] = true;
    }
    return {
      id: `agent:${a.id}`,
      label: labels.get(a.id) ?? a.name,
      values: buckets.map((_, i) => {
        if (!seen[i]) return null;
        if (metric === "requests") return sums[i];
        return weights[i] ? sums[i] / weights[i] : null;
      }),
    };
  });
}

// ---- labels -----------------------------------------------------------------

/** Short label for a bucket's midpoint: a clock time inside a day, a date beyond it. */
export function bucketLabel(bucket: Bucket, totalSpanMs: number): string {
  const mid = new Date((bucket.start + bucket.end) / 2);
  if (totalSpanMs >= 2 * 24 * 60 * 60_000) return mid.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return mid.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
}

export function formatValue(v: number, unit: string, digits: number): string {
  return `${v.toFixed(digits)}${unit}`;
}

// ---- ranges -----------------------------------------------------------------

const HOUR = 60 * 60_000;
const DAY = 24 * HOUR;

export const RANK_RANGES = {
  llm: [
    { key: "1h", label: "1h", ms: HOUR, buckets: 6 },
    { key: "6h", label: "6h", ms: 6 * HOUR, buckets: 6 },
    { key: "24h", label: "24h", ms: DAY, buckets: 8 },
  ],
  agent: [
    { key: "24h", label: "24h", ms: DAY, buckets: 8 },
    { key: "7d", label: "7d", ms: 7 * DAY, buckets: 7 },
    { key: "30d", label: "30d", ms: 30 * DAY, buckets: 10 },
  ],
} as const;

export type RankSubject = keyof typeof RANK_RANGES;
