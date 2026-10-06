import { describe, it, expect } from "vitest";
import { applyStreamEvent, startStream, toStreamEvent } from "../src/chat-stream-utils";
import type { ChatMessageItem } from "../src/api";

const msg = (id: number, role: ChatMessageItem["role"], content = ""): ChatMessageItem => ({
  id, chat_id: 1, role, content, prompt_tokens: null, completion_tokens: null, latency_ms: null, created_at: "t", tool_meta: {},
});

describe("applyStreamEvent", () => {
  it("startStream keeps the existing messages and marks streaming", () => {
    const s = startStream([msg(1, "user")]);
    expect(s).toMatchObject({ liveText: "", streaming: true, error: null });
    expect(s.messages).toHaveLength(1);
  });

  it("user event appends the stored user message", () => {
    const s = applyStreamEvent(startStream([]), { type: "user", message: msg(5, "user", "hi") });
    expect(s.messages.map((m) => m.id)).toEqual([5]);
  });

  it("deltas accumulate in order", () => {
    let s = startStream([]);
    for (const t of ["he", "llo ", "world"]) s = applyStreamEvent(s, { type: "delta", text: t });
    expect(s.liveText).toBe("hello world");
  });

  it("the stored message replaces the live text", () => {
    let s = applyStreamEvent(startStream([]), { type: "delta", text: "partial" });
    s = applyStreamEvent(s, { type: "message", message: msg(9, "assistant", "partial reply") });
    expect(s.liveText).toBe("");
    expect(s.messages.map((m) => m.id)).toEqual([9]);
  });

  it("the same message id is never shown twice (stream + refetch)", () => {
    let s = startStream([msg(5, "user", "hi")]);
    s = applyStreamEvent(s, { type: "user", message: msg(5, "user", "hi") });
    s = applyStreamEvent(s, { type: "message", message: msg(6, "assistant") });
    s = applyStreamEvent(s, { type: "message", message: msg(6, "assistant") });
    expect(s.messages.map((m) => m.id)).toEqual([5, 6]);
  });

  it("a tool round trip keeps order: assistant, tool, assistant", () => {
    let s = startStream([]);
    for (const [id, role] of [[1, "assistant"], [2, "tool"], [3, "assistant"]] as const) {
      s = applyStreamEvent(s, { type: "delta", text: "x" });
      s = applyStreamEvent(s, { type: "message", message: msg(id, role) });
    }
    expect(s.messages.map((m) => m.role)).toEqual(["assistant", "tool", "assistant"]);
    expect(s.liveText).toBe("");
  });

  it("error keeps what was stored, records the detail and drops the unfinished text", () => {
    let s = applyStreamEvent(startStream([msg(1, "user")]), { type: "delta", text: "half" });
    s = applyStreamEvent(s, { type: "error", detail: "model exploded" });
    expect(s.error).toBe("model exploded");
    expect(s.liveText).toBe("");
    expect(s.messages).toHaveLength(1);
    expect(s.streaming).toBe(true); // `done` is what ends it
  });

  it("done ends streaming", () => {
    expect(applyStreamEvent(startStream([]), { type: "done" }).streaming).toBe(false);
  });

  it("does not mutate the previous state", () => {
    const before = startStream([msg(1, "user")]);
    const snapshot = JSON.stringify(before);
    applyStreamEvent(before, { type: "message", message: msg(2, "assistant") });
    applyStreamEvent(before, { type: "delta", text: "x" });
    expect(JSON.stringify(before)).toBe(snapshot);
  });
});

describe("toStreamEvent", () => {
  const sse = (event: string, data: unknown) => ({ event, data: typeof data === "string" ? data : JSON.stringify(data) });

  it("maps each known event", () => {
    expect(toStreamEvent(sse("delta", { text: "x" }))).toEqual({ type: "delta", text: "x" });
    expect(toStreamEvent(sse("done", { message_ids: [] }))).toEqual({ type: "done" });
    expect(toStreamEvent(sse("error", { detail: "bad" }))).toEqual({ type: "error", detail: "bad" });
    expect(toStreamEvent(sse("user", msg(1, "user")))).toMatchObject({ type: "user" });
    expect(toStreamEvent(sse("message", msg(2, "assistant")))).toMatchObject({ type: "message" });
  });

  it("an error without a detail still says something", () => {
    expect((toStreamEvent(sse("error", {})) as { detail: string }).detail).toMatch(/failed/i);
  });

  it("unknown events, bad JSON, non-objects and malformed deltas are ignored, not thrown", () => {
    expect(toStreamEvent(sse("from_the_future", { a: 1 }))).toBeNull();
    expect(toStreamEvent(sse("delta", "{not json"))).toBeNull();
    expect(toStreamEvent(sse("delta", "42"))).toBeNull();
    expect(toStreamEvent(sse("delta", { text: 5 }))).toBeNull();
    expect(toStreamEvent(sse("delta", "null"))).toBeNull();
  });
});
