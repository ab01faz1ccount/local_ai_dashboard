import { describe, it, expect } from "vitest";
import { placementSummary, inspectProblems, deviceWarning } from "../src/inspect-utils";
import type { RuntimeInspect } from "../src/api";

const base = (over: Partial<RuntimeInspect> = {}): RuntimeInspect => ({
  launch_command: null,
  placement: "unknown",
  checks: [],
  warnings: [],
  ok: true,
  devices: null,
  observed: { threads: 6, slots: 1, ctx_per_slot: 64000, layers_offloaded: null, layers_total: null, saw_model_load: true },
  ...over,
});

describe("placementSummary", () => {
  it("null report -> nothing", () => expect(placementSummary(null)).toBeNull());
  it("cpu is a warning, never green", () => {
    const s = placementSummary(base({ placement: "cpu" }))!;
    expect(s.tone).toBe("warn");
    expect(s.text).toContain("CPU");
  });
  it("gpu shows layer counts", () => {
    const s = placementSummary(
      base({ placement: "gpu", observed: { ...base().observed, layers_offloaded: 33, layers_total: 33 } })
    )!;
    expect(s.tone).toBe("ok");
    expect(s.text).toContain("33/33");
  });
  it("partial is a warning", () => expect(placementSummary(base({ placement: "partial" }))!.tone).toBe("warn"));
  it("unknown is 'waiting', not a verdict", () => expect(placementSummary(base())!.tone).toBe("wait"));
});

describe("inspectProblems", () => {
  it("lists warnings then mismatched checks; ok/unknown checks are not problems", () => {
    const r = base({
      warnings: ["W1"],
      checks: [
        { key: "ctx_size -> server", status: "mismatch", expected: 16384, actual: 64000, detail: "startup log" },
        { key: "threads -> server", status: "ok", expected: 6, actual: 6, detail: "" },
        { key: "model on GPU", status: "unknown", expected: "99", actual: null, detail: "" },
      ],
    });
    const p = inspectProblems(r);
    expect(p).toHaveLength(2);
    expect(p[0]).toBe("W1");
    expect(p[1]).toContain("16384");
    expect(p[1]).toContain("64000");
  });
  it("no report -> none", () => expect(inspectProblems(undefined)).toEqual([]));
});

describe("deviceWarning", () => {
  it("probed + no gpus -> warning", () =>
    expect(deviceWarning({ probed: true, devices: [], gpus: [] })).toContain("CPU"));
  it("a gpu present -> no warning", () => {
    const d = { id: "CUDA0", description: "x", is_gpu: true };
    expect(deviceWarning({ probed: true, devices: [d], gpus: [d] })).toBeNull();
  });
  it("could not run the binary -> stay silent (unknown is not 'no GPU')", () =>
    expect(deviceWarning({ probed: false, devices: [], gpus: [] })).toBeNull());
  it("missing -> silent", () => expect(deviceWarning(null)).toBeNull());
});
