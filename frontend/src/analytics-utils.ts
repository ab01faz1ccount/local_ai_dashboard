/**
 * frontend/src/analytics-utils.ts
 *
 * Pure helpers behind AnalyticsPage/LineChart: turning the backend's
 * newest-first metrics history into chart points, picking a time
 * range, and building an SVG path. No React/DOM in here so every edge
 * (gaps, a single point, a flat line) is cheap to test exhaustively.
 */

import type { MetricsSnapshotItem } from "./api";

export type MetricKey = "tokens_per_sec" | "last_latency_ms" | "cpu_percent" | "gpu_percent" | "ram_percent" | "vram_percent";

export interface ChartPoint {
  /** ms since epoch */
  t: number;
  v: number;
}

export type TimeRange = "15m" | "1h" | "6h" | "24h";

export const RANGE_MS: Record<TimeRange, number> = {
  "15m": 15 * 60_000,
  "1h": 60 * 60_000,
  "6h": 6 * 60 * 60_000,
  "24h": 24 * 60 * 60_000,
};

export function rangeToSince(range: TimeRange, now: number = Date.now()): string {
  return new Date(now - RANGE_MS[range]).toISOString();
}

function metricValue(s: MetricsSnapshotItem, key: MetricKey): number | null {
  switch (key) {
    case "ram_percent":
      return s.ram_used_mb != null && s.ram_total_mb ? (s.ram_used_mb / s.ram_total_mb) * 100 : null;
    case "vram_percent":
      return s.vram_used_mb != null && s.vram_total_mb ? (s.vram_used_mb / s.vram_total_mb) * 100 : null;
    default:
      return s[key];
  }
}

/** The backend returns newest-first; a chart wants oldest-first. Snapshots
 * whose value for `key` is null (e.g. tokens_per_sec while idle, or
 * anything GPU-related on a machine with no GPU) are dropped, not drawn as 0
 * -- a missing sample is not the same as a measured zero. */
export function buildSeries(snapshots: MetricsSnapshotItem[], key: MetricKey): ChartPoint[] {
  const points: ChartPoint[] = [];
  for (let i = snapshots.length - 1; i >= 0; i--) {
    const v = metricValue(snapshots[i], key);
    const t = Date.parse(snapshots[i].timestamp);
    if (v != null && Number.isFinite(v) && Number.isFinite(t)) points.push({ t, v });
  }
  return points;
}

export interface SeriesStats {
  min: number;
  max: number;
  avg: number;
  last: number;
}

export function seriesStats(points: ChartPoint[]): SeriesStats | null {
  if (points.length === 0) return null;
  let min = Infinity;
  let max = -Infinity;
  let sum = 0;
  for (const p of points) {
    if (p.v < min) min = p.v;
    if (p.v > max) max = p.v;
    sum += p.v;
  }
  return { min, max, avg: sum / points.length, last: points[points.length - 1].v };
}

export interface ChartGeometry {
  /** SVG path `d` attribute; empty string when there's nothing to draw. */
  path: string;
  /** y-axis domain actually used (after padding a flat line out). */
  yMin: number;
  yMax: number;
}

interface Scale {
  x: (t: number) => number;
  y: (v: number) => number;
  yMin: number;
  yMax: number;
}

/** The mapping from data to pixels, shared by the line and by the hover
 * readout so the crosshair can never sit somewhere the line is not.
 * A perfectly flat series gets a +/-1 domain so it draws mid-chart instead
 * of dividing by zero; `fixedMax` pins the top of the y axis. */
function makeScale(points: ChartPoint[], width: number, height: number, pad: number, fixedMax?: number): Scale {
  const stats = seriesStats(points)!;
  let yMin = fixedMax != null ? 0 : Math.min(0, stats.min);
  let yMax = fixedMax ?? stats.max;
  if (yMax - yMin === 0) {
    yMin -= 1;
    yMax += 1;
  }
  const tMin = points[0].t;
  const tMax = points[points.length - 1].t;
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;
  return {
    x: (t) => (tMax === tMin ? width / 2 : pad + ((t - tMin) / (tMax - tMin)) * innerW),
    y: (v) => pad + innerH - ((v - yMin) / (yMax - yMin)) * innerH,
    yMin,
    yMax,
  };
}

/** Maps points into a `width` x `height` box (with `pad` px margin).
 * A single point becomes a short horizontal tick so it's still visible
 * (`fixedMax` pins the top of the y axis, used for 0-100 percent metrics so
 * a quiet 3% CPU doesn't look maxed out). */
export function buildChartPath(
  points: ChartPoint[],
  width: number,
  height: number,
  pad = 4,
  fixedMax?: number
): ChartGeometry {
  if (points.length === 0) return { path: "", yMin: 0, yMax: fixedMax ?? 1 };

  const { x, y, yMin, yMax } = makeScale(points, width, height, pad, fixedMax);

  if (points.length === 1) {
    const cx = x(points[0].t);
    const cy = y(points[0].v);
    return { path: `M ${(cx - 6).toFixed(1)} ${cy.toFixed(1)} L ${(cx + 6).toFixed(1)} ${cy.toFixed(1)}`, yMin, yMax };
  }
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`).join(" ");
  return { path, yMin, yMax };
}

/** The same line closed down to the baseline, for the soft fill under it. "" for < 2 points. */
export function buildAreaPath(points: ChartPoint[], width: number, height: number, pad = 4, fixedMax?: number): string {
  if (points.length < 2) return "";
  const { x, y, yMin } = makeScale(points, width, height, pad, fixedMax);
  const line = points.map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`).join(" ");
  const base = y(Math.max(yMin, 0)).toFixed(1);
  return `${line} L ${x(points[points.length - 1].t).toFixed(1)} ${base} L ${x(points[0].t).toFixed(1)} ${base} Z`;
}

/** Where point `index` is drawn (same scale as buildChartPath). */
export function pointPosition(
  points: ChartPoint[],
  index: number,
  width: number,
  height: number,
  pad = 4,
  fixedMax?: number
): { x: number; y: number } | null {
  if (index < 0 || index >= points.length) return null;
  const { x, y } = makeScale(points, width, height, pad, fixedMax);
  return { x: x(points[index].t), y: y(points[index].v) };
}

/** Index of the point drawn closest (horizontally) to viewBox x `viewX`; -1 for no points. */
export function nearestPointIndex(points: ChartPoint[], viewX: number, width: number, pad = 4): number {
  if (points.length === 0) return -1;
  const { x } = makeScale(points, width, 1, pad);
  let best = 0;
  let bestDist = Infinity;
  for (let i = 0; i < points.length; i++) {
    const d = Math.abs(x(points[i].t) - viewX);
    if (d < bestDist) {
      bestDist = d;
      best = i;
    }
  }
  return best;
}

export function formatMetric(value: number | null | undefined, unit: string, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}${unit}`;
}

/** Whether the series has any gap (consecutive samples further apart than
 * `factor` x the median spacing) -- the chart notes it instead of silently
 * drawing a straight line across hours when nothing was running. */
export function hasGaps(points: ChartPoint[], factor = 5): boolean {
  if (points.length < 3) return false;
  const deltas = points.slice(1).map((p, i) => p.t - points[i].t).sort((a, b) => a - b);
  const median = deltas[Math.floor(deltas.length / 2)];
  if (median <= 0) return false;
  return deltas[deltas.length - 1] > median * factor;
}
