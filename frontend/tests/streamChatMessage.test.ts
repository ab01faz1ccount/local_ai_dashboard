import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ApiError, api, getStoredToken, setStoredToken } from "../src/api";
import type { SseEvent } from "../src/sse-utils";

function streamBody(chunks: string[], opts: { hangAfter?: boolean } = {}): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i < chunks.length) controller.enqueue(enc.encode(chunks[i++]));
      else if (!opts.hangAfter) controller.close();
    },
  });
}

const respond = (body: ReadableStream<Uint8Array> | null, init: ResponseInit = { status: 200 }) =>
  Promise.resolve(new Response(body, init));

beforeEach(() => {
  setStoredToken("tok");
});
afterEach(() => vi.restoreAllMocks());

describe("api.streamChatMessage", () => {
  it("posts to the stream endpoint with auth and the message", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(() => respond(streamBody([])));
    await api.streamChatMessage(7, "hello", () => {}, { temperature: 0.2 });
    const [url, init] = f.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/v1\/chats\/7\/messages\/stream$/);
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ content: "hello", temperature: 0.2 });
    expect(JSON.stringify((init as RequestInit).headers)).toContain("Bearer tok");
  });

  it("delivers events as they arrive, including ones split across network chunks", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      respond(streamBody(['event: delta\ndata: {"te', 'xt":"a"}\n\nevent: delta\ndata: {"text":"b"}\n\n', "event: done\ndata: {}\n\n"]))
    );
    const got: SseEvent[] = [];
    await api.streamChatMessage(1, "x", (e) => got.push(e));
    expect(got.map((e) => e.event)).toEqual(["delta", "delta", "done"]);
    expect(got[0].data).toBe('{"text":"a"}');
  });

  it("a multi-byte character split across chunks is decoded correctly", async () => {
    const bytes = new TextEncoder().encode('event: delta\ndata: {"text":"س"}\n\n');
    const cut = bytes.indexOf(0xd8) + 1; // between the two bytes of "س"
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, cut));
        c.enqueue(bytes.slice(cut));
        c.close();
      },
    });
    vi.spyOn(globalThis, "fetch").mockImplementation(() => respond(body));
    const got: SseEvent[] = [];
    await api.streamChatMessage(1, "x", (e) => got.push(e));
    expect(JSON.parse(got[0].data).text).toBe("س");
  });

  it("a pre-flight error becomes an ApiError carrying the server's message", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      respond(streamBody([JSON.stringify({ detail: "runtime is not online (state: OFFLINE)" })]), { status: 409 })
    );
    await expect(api.streamChatMessage(1, "x", () => {})).rejects.toMatchObject({ status: 409, message: expect.stringContaining("not online") });
  });

  it("401 clears the stored token", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => respond(null, { status: 401 }));
    await expect(api.streamChatMessage(1, "x", () => {})).rejects.toBeInstanceOf(ApiError);
    expect(getStoredToken()).toBeNull();
  });

  it("aborting mid-stream resolves quietly (a Stop is not an error) after delivering what arrived", async () => {
    const ctrl = new AbortController();
    vi.spyOn(globalThis, "fetch").mockImplementation((_u, init) => {
      const signal = init?.signal as AbortSignal;
      const enc = new TextEncoder();
      let sent = false;
      // like a real fetch body: the read rejects with AbortError once the request is aborted
      const body = new ReadableStream<Uint8Array>({
        pull(controller) {
          if (!sent) {
            sent = true;
            controller.enqueue(enc.encode('event: delta\ndata: {"text":"a"}\n\n'));
            return;
          }
          return new Promise<void>((_res, rej) =>
            signal.addEventListener("abort", () => {
              const err = new DOMException("aborted", "AbortError");
              controller.error(err);
              rej(err);
            })
          );
        },
      });
      return respond(body);
    });
    const got: SseEvent[] = [];
    await api.streamChatMessage(1, "x", (e) => { got.push(e); ctrl.abort(); }, { signal: ctrl.signal });
    expect(got.map((e) => e.event)).toEqual(["delta"]);
  });

  it("a read error that is NOT an abort rejects", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      respond(new ReadableStream<Uint8Array>({ start(c) { c.error(new TypeError("network dropped")); } }))
    );
    await expect(api.streamChatMessage(1, "x", () => {})).rejects.toThrow("network dropped");
  });

  it("aborting before the response arrives resolves quietly", async () => {
    const ctrl = new AbortController();
    vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.reject(new DOMException("aborted", "AbortError")));
    ctrl.abort();
    await expect(api.streamChatMessage(1, "x", () => {}, { signal: ctrl.signal })).resolves.toBeUndefined();
  });

  it("a network failure that is NOT an abort still rejects", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(api.streamChatMessage(1, "x", () => {})).rejects.toThrow("Failed to fetch");
  });
});
