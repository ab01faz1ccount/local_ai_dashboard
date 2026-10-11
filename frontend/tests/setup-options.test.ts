import { describe, it, expect } from "vitest";
import { SETUP_OPTIONS, filterSetupOptions } from "../src/setup-options";

describe("filterSetupOptions", () => {
  it("all -> every option, in order", () => {
    expect(filterSetupOptions("all").map((o) => o.id)).toEqual(["run-llm", "add-model", "add-agent"]);
  });
  it("llm -> only LLM options", () => {
    expect(filterSetupOptions("llm").map((o) => o.id)).toEqual(["run-llm", "add-model"]);
  });
  it("agent -> only agent options", () => {
    expect(filterSetupOptions("agent").map((o) => o.id)).toEqual(["add-agent"]);
  });
  it("every option has steps to show on its card", () => {
    for (const o of SETUP_OPTIONS) expect(o.steps.length > 0).toBe(true);
  });
});
