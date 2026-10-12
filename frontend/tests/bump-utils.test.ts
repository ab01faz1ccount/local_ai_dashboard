import { describe, it, expect } from "vitest";
import {
  MAX_SERIES, agentEntries, bucketAverages, bucketLabel, buildBump, bumpLayout, makeBuckets, rankValues, runtimeEntries, uniqueLabels,
} from "../src/bump-utils.ts";

const T0 = Date.UTC(2026, 0, 1, 0, 0, 0);
const MIN = 60_000;

describe("makeBuckets", () => {
  it("splits the span into equal, contiguous buckets", () => {
    const b = makeBuckets(0, 600, 3);
    expect(b).toEqual([{ start: 0, end: 200 }, { start: 200, end: 400 }, { start: 400, end: 600 }]);
  });
  it("always gives at least one bucket", () => expect(makeBuckets(0, 10, 0).length).toBe(1));
});

describe("bucketAverages", () => {
  const b = makeBuckets(0, 300, 3);
  it("averages what falls in each bucket and leaves empty ones null", () => {
    expect(bucketAverages([{ t: 10, v: 10 }, { t: 20, v: 20 }, { t: 250, v: 7 }], b)).toEqual([15, null, 7]);
  });
  it("a point exactly on a boundary goes to the later bucket; the very end to the last", () => {
    expect(bucketAverages([{ t: 100, v: 5 }, { t: 300, v: 9 }], b)).toEqual([null, 5, 9]);
  });
  it("ignores points outside the window and non-finite values", () => {
    expect(bucketAverages([{ t: -1, v: 1 }, { t: 301, v: 1 }, { t: 5, v: NaN }], b)).toEqual([null, null, null]);
  });
});

describe("rankValues", () => {
  it("ranks higher-is-better and lower-is-better", () => {
    expect(rankValues([5, 9, 1], "higher")).toEqual([2, 1, 3]);
    expect(rankValues([5, 9, 1], "lower")).toEqual([2, 3, 1]);
  });
  it("ties share the better rank and the next is skipped", () => {
    expect(rankValues([9, 9, 3], "higher")).toEqual([1, 1, 3]);
  });
  it("a missing value has no rank and does not take a place", () => {
    expect(rankValues([null, 4, 8], "higher")).toEqual([null, 2, 1]);
  });
});

describe("buildBump", () => {
  const e = (id: string, values: (number | null)[]) => ({ id, label: id, values });

  it("ranks per bucket and shows overtakes", () => {
    const m = buildBump([e("a", [10, 5]), e("b", [8, 9])], "higher");
    expect(m.series.find((s) => s.id === "a")!.ranks).toEqual([1, 2]);
    expect(m.series.find((s) => s.id === "b")!.ranks).toEqual([2, 1]);
    expect(m.maxRank).toBe(2);
  });

  it("a gap stays a gap instead of becoming last place", () => {
    const m = buildBump([e("a", [10, null, 10]), e("b", [1, 1, 1])], "higher");
    expect(m.series.find((s) => s.id === "a")!.ranks).toEqual([1, null, 1]);
    expect(m.series.find((s) => s.id === "b")!.ranks[1]).toBe(1);
  });

  it("entries with no data at all are not drawn and not counted", () => {
    const m = buildBump([e("a", [1, 2]), e("ghost", [null, null])], "higher");
    expect(m.series.map((s) => s.id)).toEqual(["a"]);
    expect(m.total).toBe(1);
  });

  it("keeps only the best MAX_SERIES, reports how many are hidden, and re-ranks the rest compactly", () => {
    const many = Array.from({ length: 10 }, (_, i) => e(`s${String(i).padStart(2, "0")}`, [100 - i, 100 - i]));
    const m = buildBump(many, "higher");
    expect(m.series.length).toBe(MAX_SERIES);
    expect(m.hidden).toBe(2);
    expect(m.total).toBe(10);
    expect(m.maxRank).toBe(MAX_SERIES);
    expect(m.series.map((s) => s.id)).toEqual(many.slice(0, 8).map((x) => x.id));
  });

  it("an entry with more data outranks one with a better average but fewer samples", () => {
    const m = buildBump([e("full", [5, 5, 5]), e("brief", [9, null, null])], "higher", 1);
    expect(m.series.map((s) => s.id)).toEqual(["full"]);
  });

  it("with equal amounts of data the better average rank is kept", () => {
    const m = buildBump([e("a-slow", [1, 1]), e("b-fast", [9, 9])], "higher", 1);
    expect(m.series.map((s) => s.id)).toEqual(["b-fast"]);
  });

  it("color slots wrap inside the palette however many entries exist", () => {
    const many = Array.from({ length: 12 }, (_, i) => e(`s${String(i).padStart(2, "0")}`, [i, i]));
    expect(buildBump(many, "higher", 12).series.every((s) => s.slot >= 0 && s.slot < MAX_SERIES)).toBe(true);
  });

  it("colors follow the entity: hiding another entry never repaints the survivors", () => {
    const base = [e("a", [3, 3]), e("b", [2, 2]), e("c", [1, 1])];
    const all = buildBump(base, "higher");
    const fewer = buildBump(base.filter((x) => x.id !== "b"), "higher");
    expect(all.series.find((s) => s.id === "c")!.slot).toBe(2);
    expect(fewer.series.find((s) => s.id === "c")!.slot).toBe(1); // sorted-id slots among what exists...
    const capped = buildBump(base, "higher", 2);
    expect(capped.series.find((s) => s.id === "a")!.slot).toBe(all.series.find((s) => s.id === "a")!.slot);
    expect(capped.series.find((s) => s.id === "b")!.slot).toBe(all.series.find((s) => s.id === "b")!.slot);
  });

  it("nothing to rank is an empty model, not an error", () => {
    expect(buildBump([], "higher")).toEqual({ series: [], total: 0, hidden: 0, maxRank: 0 });
  });
});

describe("bumpLayout", () => {
  const pad = { left: 10, right: 10, top: 10, bottom: 10 };
  const series = (ranks: (number | null)[]) => [{ id: "a", label: "a", slot: 0, values: ranks.map((r) => (r == null ? null : r * 10)), ranks }];

  it("places rank 1 at the top, the last rank at the bottom, buckets left to right", () => {
    const l = bumpLayout(series([1, 3]), 2, 3, 120, 100, pad);
    expect(l.series[0].points.map((p) => [p.x, p.y])).toEqual([[10, 10], [110, 90]]);
    expect(l.rowYs).toEqual([10, 50, 90]);
    expect(l.xs).toEqual([10, 110]);
  });

  it("draws one path per unbroken run and none across a gap", () => {
    const l = bumpLayout(series([1, 2, null, 2, 1]), 5, 2, 200, 100, pad);
    expect(l.series[0].paths.length).toBe(2);
    expect(l.series[0].points.length).toBe(4);
  });

  it("a lone point has a dot but no path", () => {
    const l = bumpLayout(series([null, 1, null]), 3, 1, 100, 100, pad);
    expect(l.series[0].paths).toEqual([]);
    expect(l.series[0].points.length).toBe(1);
  });

  it("a single bucket and a single rank sit in the middle", () => {
    const l = bumpLayout(series([1]), 1, 1, 100, 100, pad);
    expect(l.series[0].points[0]).toMatchObject({ x: 50, y: 50 });
  });
});

describe("data builders", () => {
  const buckets = makeBuckets(T0, T0 + 60 * MIN, 2);
  const rt = (id: number, name: string, model_name: string | null = null) => ({ id, name, model_name }) as never;
  const snap = (min: number, tps: number | null, lat: number | null = null) =>
    ({ timestamp: new Date(T0 + min * MIN).toISOString(), tokens_per_sec: tps, last_latency_ms: lat, cpu_percent: null, ram_used_mb: null, ram_total_mb: null, vram_used_mb: null, vram_total_mb: null, gpu_percent: null }) as never;

  it("runtimes are bucketed by their measured speed; idle samples (null) are not zeros", () => {
    const out = runtimeEntries([rt(1, "r1", "Qwen")], { 1: [snap(5, 20), snap(10, 30), snap(40, null)] }, "tokens_per_sec", buckets);
    expect(out[0]).toMatchObject({ id: "runtime:1", label: "Qwen", values: [25, null] });
  });

  it("two runtimes with the same label are told apart by id", () => {
    const labels = uniqueLabels([{ id: 1 }, { id: 2 }, { id: 3 }], (x) => (x.id === 3 ? "solo" : "same"));
    expect([...labels.values()]).toEqual(["same #1", "same #2", "solo"]);
  });

  const agent = (id: number, name: string) => ({ id, name }) as never;
  const sess = (agent_id: number, min: number, requests: number, tps: number | null, lat: number | null = null) =>
    ({ agent_id, started_at: new Date(T0 + min * MIN).toISOString(), requests_count: requests, avg_tokens_per_sec: tps, avg_latency_ms: lat }) as never;

  it("agent requests are summed per bucket", () => {
    const out = agentEntries([agent(1, "A")], [sess(1, 5, 3, null), sess(1, 6, 4, null), sess(1, 45, 1, null), sess(2, 5, 99, null)], "requests", buckets);
    expect(out[0].values).toEqual([7, 1]);
  });

  it("agent speed is weighted by each session's request count", () => {
    const out = agentEntries([agent(1, "A")], [sess(1, 5, 1, 100), sess(1, 6, 9, 10)], "avg_tokens_per_sec", buckets);
    expect(out[0].values[0]).toBeCloseTo(19, 5); // (100*1 + 10*9) / 10
  });

  it("sessions with no measured speed or no requests don't count toward speed", () => {
    const out = agentEntries([agent(1, "A")], [sess(1, 5, 5, null), sess(1, 6, 0, 50)], "avg_tokens_per_sec", buckets);
    expect(out[0].values).toEqual([null, null]);
  });

  it("a non-finite speed is ignored rather than poisoning the average", () => {
    const out = agentEntries([agent(1, "A")], [sess(1, 5, 4, NaN), sess(1, 6, 4, 20)], "avg_tokens_per_sec", buckets);
    expect(out[0].values[0]).toBe(20);
  });

  it("sessions outside the window are ignored", () => {
    expect(agentEntries([agent(1, "A")], [sess(1, -30, 5, 5), sess(1, 90, 5, 5)], "requests", buckets)[0].values).toEqual([null, null]);
  });
});

describe("bucketLabel", () => {
  it("is a clock time within a day and a date beyond two days", () => {
    const b = { start: T0, end: T0 + 60 * MIN };
    expect(bucketLabel(b, 6 * 60 * MIN)).toMatch(/\d\d:\d\d/);
    expect(bucketLabel(b, 7 * 24 * 60 * MIN)).not.toMatch(/\d\d:\d\d/);
  });
});
