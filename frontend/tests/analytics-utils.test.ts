import { describe, it, expect } from "vitest";
import {
  buildChartPath, buildSeries, formatMetric, hasGaps, rangeToSince, seriesStats, RANGE_MS, type ChartPoint,
} from "../src/analytics-utils";
import type { MetricsSnapshotItem } from "../src/api";

const snap = (o: Partial<MetricsSnapshotItem> & { timestamp: string }): MetricsSnapshotItem => ({
  id: 1, runtime_id: 1, cpu_percent: null, ram_used_mb: null, ram_total_mb: null, gpu_percent: null,
  vram_used_mb: null, vram_total_mb: null, tokens_per_sec: null, last_latency_ms: null, ...o,
});

describe("rangeToSince", () => {
  it("subtracts the range from now", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(rangeToSince("1h", now)).toBe("2026-01-01T11:00:00.000Z");
    expect(rangeToSince("15m", now)).toBe("2026-01-01T11:45:00.000Z");
    expect(rangeToSince("24h", now)).toBe("2025-12-31T12:00:00.000Z");
  });
  it("ranges are strictly increasing", () => {
    expect(RANGE_MS["15m"]).toBeLessThan(RANGE_MS["1h"]);
    expect(RANGE_MS["1h"]).toBeLessThan(RANGE_MS["6h"]);
    expect(RANGE_MS["6h"]).toBeLessThan(RANGE_MS["24h"]);
  });
});

describe("buildSeries", () => {
  it("reverses newest-first input into oldest-first", () => {
    const rows = [
      snap({ timestamp: "2026-01-01T00:00:02Z", tokens_per_sec: 3 }),
      snap({ timestamp: "2026-01-01T00:00:01Z", tokens_per_sec: 2 }),
      snap({ timestamp: "2026-01-01T00:00:00Z", tokens_per_sec: 1 }),
    ];
    expect(buildSeries(rows, "tokens_per_sec").map((p) => p.v)).toEqual([1, 2, 3]);
  });
  it("drops nulls instead of plotting them as zero", () => {
    const rows = [
      snap({ timestamp: "2026-01-01T00:00:02Z", tokens_per_sec: 5 }),
      snap({ timestamp: "2026-01-01T00:00:01Z", tokens_per_sec: null }),
    ];
    expect(buildSeries(rows, "tokens_per_sec").map((p) => p.v)).toEqual([5]);
  });
  it("a measured zero is kept", () => {
    expect(buildSeries([snap({ timestamp: "2026-01-01T00:00:00Z", cpu_percent: 0 })], "cpu_percent")).toHaveLength(1);
  });
  it("derives ram/vram percent from used/total", () => {
    const rows = [snap({ timestamp: "2026-01-01T00:00:00Z", ram_used_mb: 2048, ram_total_mb: 8192, vram_used_mb: 1000, vram_total_mb: 4000 })];
    expect(buildSeries(rows, "ram_percent")[0].v).toBe(25);
    expect(buildSeries(rows, "vram_percent")[0].v).toBe(25);
  });
  it("percent metrics are dropped when total is missing or zero (no GPU)", () => {
    const rows = [snap({ timestamp: "2026-01-01T00:00:00Z", vram_used_mb: null, vram_total_mb: null, ram_used_mb: 5, ram_total_mb: 0 })];
    expect(buildSeries(rows, "vram_percent")).toEqual([]);
    expect(buildSeries(rows, "ram_percent")).toEqual([]);
  });
  it("skips rows with an unparseable timestamp", () => {
    expect(buildSeries([snap({ timestamp: "garbage", cpu_percent: 5 })], "cpu_percent")).toEqual([]);
  });
  it("empty in, empty out", () => expect(buildSeries([], "cpu_percent")).toEqual([]));
});

describe("seriesStats", () => {
  it("min/max/avg/last", () => {
    const pts: ChartPoint[] = [{ t: 1, v: 2 }, { t: 2, v: 8 }, { t: 3, v: 5 }];
    expect(seriesStats(pts)).toEqual({ min: 2, max: 8, avg: 5, last: 5 });
  });
  it("null for no points", () => expect(seriesStats([])).toBeNull());
});

describe("buildChartPath", () => {
  it("empty points -> empty path", () => expect(buildChartPath([], 100, 50).path).toBe(""));

  it("draws an M then L commands, oldest to newest left to right", () => {
    const g = buildChartPath([{ t: 0, v: 0 }, { t: 10, v: 10 }], 100, 50, 0);
    expect(g.path).toBe("M 0.0 50.0 L 100.0 0.0");
  });

  it("higher values are drawn higher (smaller y)", () => {
    const g = buildChartPath([{ t: 0, v: 1 }, { t: 1, v: 9 }], 100, 100, 0);
    const ys = g.path.match(/[ML] [\d.]+ ([\d.]+)/g)!.map((m) => Number(m.split(" ")[2]));
    expect(ys[1]).toBeLessThan(ys[0]);
  });

  it("a single point becomes a short horizontal tick, not an empty path", () => {
    const g = buildChartPath([{ t: 5, v: 5 }], 100, 50);
    expect(g.path).toMatch(/^M [\d.]+ [\d.]+ L [\d.]+ [\d.]+$/);
    const [, x1, y1, , x2, y2] = g.path.split(" ");
    expect(y1).toBe(y2);
    expect(Number(x2)).toBeGreaterThan(Number(x1));
  });

  it("a perfectly flat series doesn't divide by zero (no NaN)", () => {
    const g = buildChartPath([{ t: 0, v: 5 }, { t: 1, v: 5 }, { t: 2, v: 5 }], 100, 50);
    expect(g.path).not.toContain("NaN");
    expect(g.yMax).toBeGreaterThan(g.yMin);
  });

  it("fixedMax pins the axis so a quiet 3% reads as low, not full-scale", () => {
    const g = buildChartPath([{ t: 0, v: 3 }, { t: 1, v: 3 }], 100, 100, 0, 100);
    expect(g.yMax).toBe(100);
    const y = Number(g.path.split(" ")[2]);
    expect(y).toBeGreaterThan(90); // near the bottom
  });

  it("identical timestamps don't produce NaN x", () => {
    const g = buildChartPath([{ t: 5, v: 1 }, { t: 5, v: 2 }], 100, 50);
    expect(g.path).not.toContain("NaN");
  });

  it("stays inside the padded box", () => {
    const g = buildChartPath([{ t: 0, v: 0 }, { t: 100, v: 100 }, { t: 200, v: 50 }], 200, 100, 10);
    for (const m of g.path.matchAll(/[ML] ([\d.]+) ([\d.]+)/g)) {
      expect(Number(m[1])).toBeGreaterThanOrEqual(10);
      expect(Number(m[1])).toBeLessThanOrEqual(190);
      expect(Number(m[2])).toBeGreaterThanOrEqual(10);
      expect(Number(m[2])).toBeLessThanOrEqual(90);
    }
  });
});

describe("hasGaps", () => {
  const evenly = (n: number, step = 15_000): ChartPoint[] => Array.from({ length: n }, (_, i) => ({ t: i * step, v: 1 }));
  it("false for evenly spaced samples", () => expect(hasGaps(evenly(10))).toBe(false));
  it("true when one interval dwarfs the typical spacing", () => {
    const pts = [...evenly(5), { t: 5 * 15_000 + 10 * 60_000, v: 1 }];
    expect(hasGaps(pts)).toBe(true);
  });
  it("false with too few points to judge", () => expect(hasGaps(evenly(2))).toBe(false));
});

describe("formatMetric", () => {
  it("formats with unit and digits", () => expect(formatMetric(12.345, " t/s", 1)).toBe("12.3 t/s"));
  it("em dash for null/undefined/NaN", () => {
    expect(formatMetric(null, "%")).toBe("—");
    expect(formatMetric(undefined, "%")).toBe("—");
    expect(formatMetric(NaN, "%")).toBe("—");
  });
  it("zero is a real value, not a dash", () => expect(formatMetric(0, "%")).toBe("0.0%"));
});
