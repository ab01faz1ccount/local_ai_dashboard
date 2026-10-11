import { describe, it, expect } from "vitest";
import { bucketActivity, countByStatus } from "../src/status-bar-utils";

const NOW = Date.parse("2026-01-01T12:00:00Z");
const HOUR = 3_600_000;
const at = (msAgo: number, tps: number | null) => ({ timestamp: new Date(NOW - msAgo).toISOString(), tokens_per_sec: tps });

describe("bucketActivity", () => {
  it("cuts the range into equal slots covering exactly [now-range, now]", () => {
    const b = bucketActivity([], HOUR, NOW, 12);
    expect(b).toHaveLength(12);
    expect(b[0].start).toBe(NOW - HOUR);
    expect(b[11].end).toBe(NOW);
    expect(b.every((x) => x.status === "none" && x.samples === 0)).toBe(true);
  });
  it("a generating sample makes its slot active; a quiet one makes it idle", () => {
    const b = bucketActivity([at(HOUR - 1000, 42), at(HOUR / 2, null)], HOUR, NOW, 2);
    expect(b[0].status).toBe("active");
    expect(b[1].status).toBe("idle");
  });
  it("one generating sample wins over idle ones in the same slot, in any order", () => {
    const a = bucketActivity([at(1000, null), at(2000, 10), at(3000, null)], HOUR, NOW, 4);
    const c = bucketActivity([at(3000, null), at(2000, 10), at(1000, null)], HOUR, NOW, 4);
    expect(a[3].status).toBe("active");
    expect(c[3].status).toBe("active");
    expect(a[3].samples).toBe(3);
  });
  it("zero tokens/s is idle, not generating", () => {
    expect(bucketActivity([at(1000, 0)], HOUR, NOW, 4)[3].status).toBe("idle");
  });
  it("samples outside the range or with a bad timestamp are ignored", () => {
    const b = bucketActivity([at(2 * HOUR, 50), { timestamp: "nope", tokens_per_sec: 5 }, at(-HOUR, 5)], HOUR, NOW, 4);
    expect(b.every((x) => x.samples === 0)).toBe(true);
  });
  it("a sample exactly at now lands in the last slot", () => {
    expect(bucketActivity([at(0, 1)], HOUR, NOW, 4)[3].status).toBe("active");
  });
});

describe("countByStatus", () => {
  it("counts each status", () => {
    const b = bucketActivity([at(HOUR - 1000, 42), at(1000, null)], HOUR, NOW, 4);
    expect(countByStatus(b)).toEqual({ active: 1, idle: 1, none: 2 });
  });
});
