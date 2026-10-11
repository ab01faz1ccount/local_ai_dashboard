import { describe, it, expect } from "vitest";
import {
  autoFreePayload, describeAutoFree, minutesFor, parseMinutes, parseSpecJson, quickConnectMessage, recommendedFirst, specFromPreset,
} from "../src/connect-utils.ts";

const policy = (over = {}) => ({ mode: "off" as const, grace_minutes: 2, idle_minutes: 10, ...over });

describe("parseMinutes", () => {
  it("accepts whole minutes in range only", () => {
    expect(parseMinutes(" 15 ")).toBe(15);
    expect(parseMinutes("1")).toBe(1);
    expect(parseMinutes("1440")).toBe(1440);
    for (const bad of ["", "0", "1441", "2.5", "-3", "ten", "5 min"]) expect(parseMinutes(bad)).toBeNull();
  });
});

describe("autoFreePayload", () => {
  it("off needs no minutes", () => expect(autoFreePayload("off", "garbage")).toEqual({ mode: "off" }));
  it("sends the minutes under the field that mode uses", () => {
    expect(autoFreePayload("idle", "20")).toEqual({ mode: "idle", idle_minutes: 20 });
    expect(autoFreePayload("after_agent", "3")).toEqual({ mode: "after_agent", grace_minutes: 3 });
  });
  it("is null for invalid minutes", () => expect(autoFreePayload("idle", "0")).toBeNull());
});

describe("describeAutoFree / minutesFor", () => {
  it("says what the policy does", () => {
    expect(describeAutoFree(policy())).toMatch(/until you stop/);
    expect(describeAutoFree(policy({ mode: "idle", idle_minutes: 7 }))).toMatch(/7 min/);
    expect(describeAutoFree(policy({ mode: "after_agent", grace_minutes: 4 }))).toMatch(/4 min after the last agent/);
  });
  it("minutesFor picks the field of the mode", () => {
    expect(minutesFor(policy({ mode: "idle", idle_minutes: 9 }))).toBe("9");
    expect(minutesFor(policy({ mode: "after_agent", grace_minutes: 3 }))).toBe("3");
  });
});

describe("spec helpers", () => {
  const preset = { id: "p", label: "L", config: { mode: "env_only" as const }, env: { OPENAI_BASE_URL: "{base_url}" } };
  it("a preset becomes a spec without sharing objects with the preset", () => {
    const spec = specFromPreset(preset, "Mine", "mycli");
    expect(spec).toEqual({ name: "Mine", executable: "mycli", config: { mode: "env_only" }, env: { OPENAI_BASE_URL: "{base_url}" } });
    spec.env!.X = "1";
    expect(preset.env).toEqual({ OPENAI_BASE_URL: "{base_url}" });
  });
  it("parseSpecJson reports bad JSON and non-objects", () => {
    expect("error" in parseSpecJson("{oops")).toBe(true);
    expect("error" in parseSpecJson("[1]")).toBe(true);
    expect("error" in parseSpecJson("null")).toBe(true);
    expect(parseSpecJson('{"name":"a"}')).toEqual({ spec: { name: "a" } });
  });
});

describe("recommendedFirst", () => {
  const f = (name: string, recommended = false) => ({ filename: name, recommended }) as never;
  it("moves the recommended file to the top and keeps the rest in order", () => {
    expect(recommendedFirst([f("a"), f("b", true), f("c")]).map((x: { filename: string }) => x.filename)).toEqual(["b", "a", "c"]);
  });
  it("does not mutate its input", () => {
    const input = [f("a"), f("b", true)];
    recommendedFirst(input);
    expect((input[0] as { filename: string }).filename).toBe("a");
  });
});

describe("quickConnectMessage", () => {
  const base = (over = {}) => ({
    created_runtime: true, plan: null, started: true, error: null, runtime: { status: "ONLINE" }, endpoint: {}, ...over,
  }) as never;
  it("a started runtime with no agent is a plain success", () => {
    expect(quickConnectMessage(base())).toEqual({ tone: "ok", text: "Created a runtime and started it." });
  });
  it("says reused when it did not create one", () => {
    expect(quickConnectMessage(base({ created_runtime: false })).text).toMatch(/Reused/);
  });
  it("a runtime that did not start is an error carrying the reason", () => {
    const r = quickConnectMessage(base({ started: false, runtime: { status: "ERROR" }, error: "port busy" }));
    expect(r.tone).toBe("error");
    expect(r.text).toMatch(/port busy/);
  });
  it("an agent that rejected a setting is a warning, not a success", () => {
    const r = quickConnectMessage(base({ agent: { name: "A" }, verification: { ok: false, checks: [] } }));
    expect(r.tone).toBe("warn");
  });
  it("a configured agent is a success naming it", () => {
    const r = quickConnectMessage(base({ agent: { name: "Hermes" }, verification: { ok: true, checks: [] } }));
    expect(r).toEqual({ tone: "ok", text: "Created a runtime, started it and configured Hermes to use it." });
  });
});
