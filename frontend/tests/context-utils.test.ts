import { describe, it, expect } from "vitest";
import {
  CONTEXT_STATUS_TEXT, CONTEXT_STATUS_TONE, FIT_TEXT, FIT_TONE, buildSegments, formatTokens, lastPromptNote,
} from "../src/context-utils";
import type { ContextCategory, ContextStatus, FitLabel } from "../src/api";

const cats = (sys = 0, tools = 0, conv = 0, res = 0): ContextCategory[] => [
  { key: "system", label: "System prompt", tokens: sys, items: 1, percent_of_window: null },
  { key: "tool_definitions", label: "Tool definitions", tokens: tools, items: 1, percent_of_window: null },
  { key: "conversation", label: "Conversation", tokens: conv, items: 1, percent_of_window: null },
  { key: "tool_results", label: "Tool results", tokens: res, items: 1, percent_of_window: null },
];

describe("fit labels", () => {
  const labels: FitLabel[] = ["GOOD", "WARNING", "LIKELY_TO_EXCEED", "NOT_RECOMMENDED", "UNKNOWN"];
  it("every label has text and a tone", () => {
    for (const l of labels) {
      expect(FIT_TEXT[l]).toBeTruthy();
      expect(FIT_TONE[l]).toBeTruthy();
    }
  });
  it("tones escalate with severity", () => {
    expect(FIT_TONE.GOOD).toBe("ok");
    expect(FIT_TONE.WARNING).toBe("warn");
    expect(FIT_TONE.LIKELY_TO_EXCEED).toBe("bad");
    expect(FIT_TONE.NOT_RECOMMENDED).toBe("bad");
    expect(FIT_TONE.UNKNOWN).toBe("muted");
  });
});

describe("context status", () => {
  const statuses: ContextStatus[] = ["OK", "HIGH", "CRITICAL", "OVER", "UNKNOWN"];
  it("every status has text and a tone", () => {
    for (const s of statuses) {
      expect(CONTEXT_STATUS_TEXT[s]).toBeTruthy();
      expect(CONTEXT_STATUS_TONE[s]).toBeTruthy();
    }
  });
  it("over the limit is as alarming as nearly full", () => {
    expect(CONTEXT_STATUS_TONE.OVER).toBe("bad");
    expect(CONTEXT_STATUS_TONE.CRITICAL).toBe("bad");
    expect(CONTEXT_STATUS_TONE.HIGH).toBe("warn");
  });
});

describe("buildSegments", () => {
  it("nothing in the context -> no segments", () => expect(buildSegments(cats(), 1000)).toEqual([]));

  it("with a window, segments are fractions of the WINDOW (the rest is headroom)", () => {
    const seg = buildSegments(cats(100, 0, 300, 0), 1000);
    expect(seg.map((s) => [s.key, s.widthPercent])).toEqual([["system", 10], ["conversation", 30]]);
    expect(seg.reduce((a, s) => a + s.widthPercent, 0)).toBe(40);
  });

  it("zero-token categories are omitted", () => {
    expect(buildSegments(cats(0, 50, 0, 50), 1000).map((s) => s.key)).toEqual(["tool_definitions", "tool_results"]);
  });

  it("over the window: normalised to the content so segments never overflow the bar", () => {
    const seg = buildSegments(cats(500, 0, 1500, 0), 1000);
    expect(seg.reduce((a, s) => a + s.widthPercent, 0)).toBeCloseTo(100);
    expect(seg[1].widthPercent).toBeCloseTo(75);
  });

  it("exactly full fills the bar", () => {
    expect(buildSegments(cats(400, 0, 600, 0), 1000).reduce((a, s) => a + s.widthPercent, 0)).toBeCloseTo(100);
  });

  it("no window: shows relative proportions of the content", () => {
    const seg = buildSegments(cats(100, 0, 300, 0), null);
    expect(seg.map((s) => s.widthPercent)).toEqual([25, 75]);
  });

  it("carries labels and token counts through", () => {
    expect(buildSegments(cats(0, 0, 10, 0), 100)[0]).toMatchObject({ key: "conversation", label: "Conversation", tokens: 10 });
  });
});

describe("formatTokens", () => {
  it("groups thousands", () => expect(formatTokens(12345)).toBe("12,345"));
  it("zero is a number, null is a dash", () => {
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(null)).toBe("—");
    expect(formatTokens(undefined)).toBe("—");
  });
});

describe("lastPromptNote", () => {
  it("none without a previous request", () => expect(lastPromptNote(100, null)).toBeNull());
  it("matches", () => expect(lastPromptNote(100, 100)).toBe("matches the previous request"));
  it("server counted more (template framing)", () => expect(lastPromptNote(100, 130)).toContain("+30"));
  it("server counted less", () => expect(lastPromptNote(100, 80)).toContain("−20"));
  it("includes the real figure", () => expect(lastPromptNote(100, 1234)).toContain("1,234"));
});
