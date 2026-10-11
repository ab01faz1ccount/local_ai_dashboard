import { describe, it, expect } from "vitest";
import { clampPercent, gaugeArc, gaugePoint, gaugeTone, percentOf } from "../src/gauge-utils";

describe("clampPercent", () => {
  it("keeps 0-100, clamps outside, and treats non-numbers as no reading", () => {
    expect(clampPercent(42)).toBe(42);
    expect(clampPercent(-5)).toBe(0);
    expect(clampPercent(140)).toBe(100);
    expect(clampPercent(null)).toBeNull();
    expect(clampPercent(undefined)).toBeNull();
    expect(clampPercent(NaN)).toBeNull();
    expect(clampPercent(Infinity)).toBeNull();
  });
});

describe("gaugePoint", () => {
  const near = (a: number, b: number) => Math.abs(a - b) < 1e-9;
  it("0% is the left end, 50% the top, 100% the right end", () => {
    const l = gaugePoint(100, 100, 80, 0);
    const t = gaugePoint(100, 100, 80, 50);
    const r = gaugePoint(100, 100, 80, 100);
    expect(near(l.x, 20) && near(l.y, 100)).toBe(true);
    expect(near(t.x, 100) && near(t.y, 20)).toBe(true);
    expect(near(r.x, 180) && near(r.y, 100)).toBe(true);
  });
});

describe("gaugeArc", () => {
  it("nothing to draw at 0 or without a reading", () => {
    expect(gaugeArc(100, 100, 80, 0)).toBe("");
    expect(gaugeArc(100, 100, 80, NaN)).toBe("");
  });
  it("starts at the left end, sweeps clockwise, never needs the large-arc flag", () => {
    expect(gaugeArc(100, 100, 80, 50)).toBe("M 20.00 100.00 A 80.00 80.00 0 0 1 100.00 20.00");
    expect(gaugeArc(100, 100, 80, 100)).toBe("M 20.00 100.00 A 80.00 80.00 0 0 1 180.00 100.00");
  });
  it("above 100 draws the same as 100", () => {
    expect(gaugeArc(100, 100, 80, 250)).toBe(gaugeArc(100, 100, 80, 100));
  });
});

describe("gaugeTone", () => {
  it("ok below 75, warn from 75, hot from 90", () => {
    expect(gaugeTone(74.9)).toBe("ok");
    expect(gaugeTone(75)).toBe("warn");
    expect(gaugeTone(89.9)).toBe("warn");
    expect(gaugeTone(90)).toBe("hot");
    expect(gaugeTone(null)).toBe("ok");
  });
});

describe("percentOf", () => {
  it("used/total as a percentage, null when it cannot be known", () => {
    expect(percentOf(2048, 8192)).toBe(25);
    expect(percentOf(null, 8192)).toBeNull();
    expect(percentOf(10, 0)).toBeNull();
    expect(percentOf(10, null)).toBeNull();
    expect(percentOf(9000, 8192)).toBe(100);
  });
});
