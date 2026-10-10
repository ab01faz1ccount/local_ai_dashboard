import { describe, it, expect } from "vitest";
import type { AgentPairing, ConfigCheckReport } from "../src/api";
import { configProblems, configSummary, pairingSummary } from "../src/pairing-utils";

function report(checks: ConfigCheckReport["checks"], extra: Partial<ConfigCheckReport> = {}): ConfigCheckReport {
  const mismatches = checks.some((c) => c.status === "mismatch");
  return {
    backend_id: "hermes",
    ok: !mismatches,
    verified: !mismatches && checks.some((c) => c.status === "ok"),
    checks,
    ...extra,
  };
}

const ok = (key: string) => ({ key, status: "ok" as const, expected: "x", actual: "x", detail: null });
const bad = (key: string, actual: string, detail: string | null = null) => ({
  key,
  status: "mismatch" as const,
  expected: "qwen",
  actual,
  detail,
});
const unreadable = (key: string) => ({ key, status: "unreadable" as const, expected: "x", actual: null, detail: "no" });

describe("configProblems", () => {
  it("nothing for null or all-ok", () => {
    expect(configProblems(null)).toEqual([]);
    expect(configProblems(report([ok("a"), ok("b")]))).toEqual([]);
  });
  it("one line per mismatch naming key, what the agent says, and what it should be", () => {
    const lines = configProblems(report([ok("a"), bad("model.default", "claude-opus-4.6 ")]));
    expect(lines).toHaveLength(1);
    expect(lines[0]).toContain("model.default");
    expect(lines[0]).toContain("«claude-opus-4.6»");
    expect(lines[0]).toContain("«qwen»");
  });
  it("calls out a stale cloud default", () => {
    const [line] = configProblems(report([bad("model.default", "claude", "x still looks like a hosted cloud model")]));
    expect(line).toContain("ابری");
  });
  it("unreadable and skipped are not problems", () => {
    expect(configProblems(report([unreadable("a"), { ...ok("b"), status: "skipped" as const }]))).toEqual([]);
  });
});

describe("configSummary", () => {
  it("null -> null", () => expect(configSummary(null)).toBeNull());
  it("mismatch -> warn with count", () => {
    const s = configSummary(report([bad("a", "z"), bad("b", "z"), ok("c")]))!;
    expect(s.tone).toBe("warn");
    expect(s.text).toContain("2");
    expect(s.icon).toBe("⚠");
  });
  it("verified -> ok", () => expect(configSummary(report([ok("a")]))!.tone).toBe("ok"));
  it("all unreadable -> unknown (wait), never ok", () => {
    const s = configSummary(report([unreadable("a")]))!;
    expect(s.tone).toBe("wait");
    expect(s.text).toContain("تأیید نشد");
  });
  it("every tone has its own icon (not color alone)", () => {
    const icons = [
      configSummary(report([bad("a", "z")]))!.icon,
      configSummary(report([ok("a")]))!.icon,
      configSummary(report([unreadable("a")]))!.icon,
    ];
    expect(new Set(icons).size).toBe(3);
  });
});

const pairing = (p: Partial<AgentPairing>): AgentPairing => ({
  watching: true,
  status: "waiting",
  requests: 0,
  verification: null,
  ...p,
});

describe("pairingSummary", () => {
  it("nothing when not watching / missing", () => {
    expect(pairingSummary(null)).toBeNull();
    expect(pairingSummary(pairing({ watching: false, status: "not_watching" }))).toBeNull();
  });
  it("waiting asks the user to send a message", () => {
    const s = pairingSummary(pairing({}))!;
    expect(s.tone).toBe("wait");
    expect(s.text).toContain("پیام");
  });
  it("paired shows the request count", () => {
    const s = pairingSummary(pairing({ status: "paired", requests: 3 }))!;
    expect(s.tone).toBe("ok");
    expect(s.text).toContain("3");
  });
  it("runtime offline warns", () => {
    expect(pairingSummary(pairing({ status: "runtime_offline" }))!.tone).toBe("warn");
  });
  it("mentions a log reset while still waiting", () => {
    expect(pairingSummary(pairing({ rebaselined: true }))!.text).toContain("ریست");
    expect(pairingSummary(pairing({ rebaselined: false }))!.text).not.toContain("ریست");
  });
  it("paired stays paired even if the log was reset", () => {
    expect(pairingSummary(pairing({ status: "paired", requests: 1, rebaselined: true }))!.tone).toBe("ok");
  });
});
