import { describe, it, expect } from "vitest";
import { canEdit, canRetry, keptForEdit, keptForRetry } from "../src/chat-actions-utils";

const m = (id: number, role: string) => ({ id, role }) as { id: number; role: "user" | "assistant" | "tool" | "system" };
const CHAT = [m(1, "user"), m(2, "assistant"), m(3, "user"), m(4, "assistant"), m(5, "tool"), m(6, "assistant")];

describe("keptForEdit", () => {
  it("drops the edited message and everything after it", () => {
    expect(keptForEdit(CHAT, 3).map((x) => x.id)).toEqual([1, 2]);
  });
  it("editing the first message leaves nothing", () => {
    expect(keptForEdit(CHAT, 1)).toEqual([]);
  });
  it("an unknown id changes nothing", () => {
    expect(keptForEdit(CHAT, 99)).toBe(CHAT);
  });
});

describe("keptForRetry", () => {
  it("keeps the question, drops the whole reply turn (assistant, tool, assistant)", () => {
    expect(keptForRetry(CHAT, 6).map((x) => x.id)).toEqual([1, 2, 3]);
  });
  it("retrying the first assistant message of a turn gives the same result", () => {
    expect(keptForRetry(CHAT, 4).map((x) => x.id)).toEqual([1, 2, 3]);
  });
  it("retrying an older reply drops the later conversation too", () => {
    expect(keptForRetry(CHAT, 2).map((x) => x.id)).toEqual([1]);
  });
  it("an unknown id changes nothing", () => {
    expect(keptForRetry(CHAT, 99)).toBe(CHAT);
  });
  it("does not mutate its input", () => {
    const before = CHAT.map((x) => x.id);
    keptForRetry(CHAT, 6);
    expect(CHAT.map((x) => x.id)).toEqual(before);
  });
});

describe("what can be done to which message", () => {
  it("edit is for user messages only", () => {
    expect(canEdit(m(1, "user"))).toBe(true);
    expect(canEdit(m(2, "assistant"))).toBe(false);
    expect(canEdit(m(5, "tool"))).toBe(false);
  });
  it("retry is for assistant replies only", () => {
    expect(canRetry(m(2, "assistant"))).toBe(true);
    expect(canRetry(m(1, "user"))).toBe(false);
    expect(canRetry(m(5, "tool"))).toBe(false);
  });
});
