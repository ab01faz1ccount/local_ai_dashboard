import { describe, it, expect } from "vitest";
import { formatDuration, toolCallStats, totalTokens } from "../src/session-utils";

describe("formatDuration", () => {
  const start = "2026-01-01T00:00:00Z";
  it("seconds", () => expect(formatDuration(start, "2026-01-01T00:00:45Z")).toBe("45s"));
  it("minutes + seconds", () => expect(formatDuration(start, "2026-01-01T00:03:05Z")).toBe("3m 5s"));
  it("hours + minutes (seconds dropped)", () => expect(formatDuration(start, "2026-01-01T02:30:59Z")).toBe("2h 30m"));
  it("an active session measures up to now", () => {
    expect(formatDuration(start, null, Date.parse("2026-01-01T00:01:00Z"))).toBe("1m 0s");
  });
  it("end before start or garbage -> dash", () => {
    expect(formatDuration("2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z")).toBe("—");
    expect(formatDuration("nope", null)).toBe("—");
  });
});

describe("totalTokens", () => {
  it("sums prompt and completion", () => expect(totalTokens({ prompt_tokens_total: 10, completion_tokens_total: 5 })).toBe(15));
});

describe("toolCallStats", () => {
  it("reads the three tool.* counts, defaulting to 0", () => {
    expect(toolCallStats({ "tool.called": 3, "tool.completed": 2, "tool.failed": 1, "inference.started": 9 })).toEqual({ called: 3, completed: 2, failed: 1 });
    expect(toolCallStats({})).toEqual({ called: 0, completed: 0, failed: 0 });
  });
});
