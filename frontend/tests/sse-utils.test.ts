import { describe, it, expect } from "vitest";
import { createSseParser } from "../src/sse-utils";

describe("createSseParser", () => {
  it("parses one complete event", () => {
    expect(createSseParser().feed('event: delta\ndata: {"text":"hi"}\n\n')).toEqual([{ event: "delta", data: '{"text":"hi"}' }]);
  });

  it("several events in one chunk, in order", () => {
    const evs = createSseParser().feed("event: a\ndata: 1\n\nevent: b\ndata: 2\n\n");
    expect(evs.map((e) => [e.event, e.data])).toEqual([["a", "1"], ["b", "2"]]);
  });

  it("an event split across chunks is held until it completes", () => {
    const p = createSseParser();
    expect(p.feed("event: delta\nda")).toEqual([]);
    expect(p.feed('ta: {"text":"x')).toEqual([]);
    expect(p.feed('"}\n\n')).toEqual([{ event: "delta", data: '{"text":"x"}' }]);
  });

  it("a chunk boundary in the middle of the blank-line terminator", () => {
    const p = createSseParser();
    expect(p.feed("event: a\ndata: 1\n")).toEqual([]);
    expect(p.feed("\nevent: b\ndata: 2\n\n").map((e) => e.event)).toEqual(["a", "b"]);
  });

  it("a trailing partial event stays buffered for the next feed", () => {
    const p = createSseParser();
    expect(p.feed("event: a\ndata: 1\n\nevent: b\ndata: ").map((e) => e.event)).toEqual(["a"]);
    expect(p.feed("2\n\n").map((e) => e.data)).toEqual(["2"]);
  });

  it("CRLF line endings", () => {
    expect(createSseParser().feed("event: a\r\ndata: 1\r\n\r\n")).toEqual([{ event: "a", data: "1" }]);
  });

  it("comments / keep-alives are ignored", () => {
    expect(createSseParser().feed(": ping\n\n")).toEqual([]);
    expect(createSseParser().feed(": ping\nevent: a\ndata: 1\n\n")).toEqual([{ event: "a", data: "1" }]);
  });

  it("event defaults to 'message' when none is given", () => {
    expect(createSseParser().feed("data: x\n\n")).toEqual([{ event: "message", data: "x" }]);
  });

  it("multi-line data is joined with newlines", () => {
    expect(createSseParser().feed("data: a\ndata: b\n\n")[0].data).toBe("a\nb");
  });

  it("a frame with no data line yields nothing", () => {
    expect(createSseParser().feed("event: a\n\n")).toEqual([]);
  });

  it("only one leading space after the colon is stripped", () => {
    expect(createSseParser().feed("data:  two\n\n")[0].data).toBe(" two");
    expect(createSseParser().feed("data:none\n\n")[0].data).toBe("none");
  });

  it("unicode and colons inside the payload survive", () => {
    expect(createSseParser().feed('event: delta\ndata: {"text":"سلام: دنیا"}\n\n')[0].data).toBe('{"text":"سلام: دنیا"}');
  });

  it("unknown fields (id:, retry:) are ignored, not fatal", () => {
    expect(createSseParser().feed("id: 7\nretry: 100\nevent: a\ndata: 1\n\n")).toEqual([{ event: "a", data: "1" }]);
  });

  it("empty feeds are harmless", () => {
    const p = createSseParser();
    expect(p.feed("")).toEqual([]);
    expect(p.feed("event: a\ndata: 1\n\n")).toHaveLength(1);
  });
});
