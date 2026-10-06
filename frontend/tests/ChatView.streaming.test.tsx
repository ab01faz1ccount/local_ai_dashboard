import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatView } from "../src/ChatView";
import type { ChatMessageItem, ChatSummary } from "../src/api";
import type { SseEvent } from "../src/sse-utils";

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  return {
    ...actual,
    api: {
      listChats: vi.fn(), listRuntimes: vi.fn(), listAgents: vi.fn(), listAgentBackends: vi.fn(),
      getChatMessages: vi.fn(), streamChatMessage: vi.fn(), getChatContext: vi.fn(), listMcpServers: vi.fn(),
      createChat: vi.fn(), deleteChat: vi.fn(), searchInChat: vi.fn(), globalSearch: vi.fn(),
    },
  };
});
import { api } from "../src/api";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const chat: ChatSummary = { id: 1, runtime_id: 1, agent_id: null, title: "t", project_id: null, created_at: "x", updated_at: "x" };
const msg = (id: number, role: ChatMessageItem["role"], content: string): ChatMessageItem => ({
  id, chat_id: 1, role, content, prompt_tokens: null, completion_tokens: null, latency_ms: null, created_at: "t", tool_meta: {},
});
const frame = (event: string, data: unknown): SseEvent => ({ event, data: JSON.stringify(data) });

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Object.values(m).forEach((f) => f.mockReset());
  m.listChats.mockResolvedValue([chat]);
  m.listRuntimes.mockResolvedValue([{ id: 1, name: "rt" }]);
  m.listAgents.mockResolvedValue([]);
  m.listAgentBackends.mockResolvedValue([]);
  m.getChatMessages.mockResolvedValue([]);
  m.getChatContext.mockRejectedValue(new Error("not needed here"));
  m.listMcpServers.mockResolvedValue([]);
});

async function openAndType(text: string) {
  render(<ChatView />);
  const box = await screen.findByPlaceholderText("Message this LLM…");
  await userEvent.type(box, text);
  return box;
}

describe("ChatView streaming send", () => {
  it("shows the reply growing live, then swaps it for the stored message", async () => {
    let emit!: (e: SseEvent) => void;
    let finish!: () => void;
    m.streamChatMessage.mockImplementation((_id: number, _c: string, onEvent: (e: SseEvent) => void) => {
      emit = onEvent;
      return new Promise<void>((res) => (finish = res));
    });
    await openAndType("hello");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByTestId("live-reply")).toHaveTextContent("Thinking…");
    act(() => emit(frame("user", msg(10, "user", "hello"))));
    act(() => emit(frame("delta", { text: "Hi " })));
    act(() => emit(frame("delta", { text: "there" })));
    expect(screen.getByTestId("live-reply")).toHaveTextContent("Hi there");
    expect(screen.getByText("hello")).toBeInTheDocument();

    m.getChatMessages.mockResolvedValue([msg(10, "user", "hello"), msg(11, "assistant", "Hi there!")]);
    act(() => emit(frame("message", msg(11, "assistant", "Hi there!"))));
    expect(screen.getByTestId("live-reply")).toHaveTextContent("Thinking…"); // text cleared, still waiting for done
    await act(async () => {
      emit(frame("done", { message_ids: [11] }));
      finish();
    });
    await waitFor(() => expect(screen.queryByTestId("live-reply")).not.toBeInTheDocument());
    expect(screen.getByText("Hi there!")).toBeInTheDocument();
    expect(screen.getAllByText("hello")).toHaveLength(1); // not duplicated by the refetch
  });

  it("clears the input right away and sends exactly what was typed", async () => {
    m.streamChatMessage.mockImplementation(() => new Promise<void>(() => {}));
    const box = await openAndType("do the thing");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(box).toHaveValue(""));
    expect(m.streamChatMessage.mock.calls[0][0]).toBe(1);
    expect(m.streamChatMessage.mock.calls[0][1]).toBe("do the thing");
  });

  it("while streaming, Send becomes Stop; clicking Stop aborts the request", async () => {
    let signal!: AbortSignal;
    m.streamChatMessage.mockImplementation((_i: number, _c: string, _e: unknown, opts: { signal: AbortSignal }) => {
      signal = opts.signal;
      return new Promise<void>((res) => opts.signal.addEventListener("abort", () => res()));
    });
    await openAndType("hi");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    const stop = await screen.findByRole("button", { name: "Stop" });
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
    expect(signal.aborted).toBe(false);
    await userEvent.click(stop);
    expect(signal.aborted).toBe(true);
    // afterwards it is back to Send and re-syncs with the server (which kept the partial reply)
    expect(await screen.findByRole("button", { name: "Send" })).toBeInTheDocument();
    await waitFor(() => expect(m.getChatMessages.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("a stream `error` event is shown and what was stored stays", async () => {
    m.streamChatMessage.mockImplementation(async (_i: number, _c: string, onEvent: (e: SseEvent) => void) => {
      onEvent(frame("user", msg(10, "user", "hello")));
      onEvent(frame("error", { detail: "llama-server returned 500: boom" }));
      onEvent(frame("done", { message_ids: [] }));
    });
    m.getChatMessages.mockResolvedValueOnce([]).mockResolvedValue([msg(10, "user", "hello")]);
    await openAndType("hello");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/llama-server returned 500: boom/)).toBeInTheDocument();
    expect(screen.getByText("hello")).toBeInTheDocument();
  });

  it("a pre-flight failure puts the text back in the input so nothing is lost", async () => {
    m.streamChatMessage.mockRejectedValue(new Error(JSON.stringify({ detail: "runtime is not online (state: OFFLINE)" })));
    const box = await openAndType("important message");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/runtime is not online/)).toBeInTheDocument();
    expect(box).toHaveValue("important message");
    expect(screen.queryByTestId("live-reply")).not.toBeInTheDocument();
  });

  it("Enter sends (Shift+Enter does not)", async () => {
    m.streamChatMessage.mockResolvedValue(undefined);
    const box = await openAndType("a");
    await userEvent.type(box, "{Shift>}{Enter}{/Shift}b");
    expect(m.streamChatMessage).not.toHaveBeenCalled();
    await userEvent.type(box, "{Enter}");
    await waitFor(() => expect(m.streamChatMessage).toHaveBeenCalledTimes(1));
  });

  it("a tool step shows up as its own bubbles while streaming", async () => {
    m.streamChatMessage.mockImplementation(async (_i: number, _c: string, onEvent: (e: SseEvent) => void) => {
      onEvent(frame("message", { ...msg(11, "assistant", ""), tool_meta: { calls: [{ id: "c1", name: "1__echo", arguments: {} }] } }));
      onEvent(frame("message", { ...msg(12, "tool", "echoed"), tool_meta: { name: "1__echo", status: "ok" } }));
      onEvent(frame("message", msg(13, "assistant", "all done")));
      onEvent(frame("done", { message_ids: [11, 12, 13] }));
    });
    const stored = [
      { ...msg(11, "assistant", ""), tool_meta: { calls: [{ id: "c1", name: "1__echo", arguments: {} }] } },
      { ...msg(12, "tool", "echoed"), tool_meta: { name: "1__echo", status: "ok" } },
      msg(13, "assistant", "all done"),
    ];
    m.getChatMessages.mockResolvedValueOnce([]).mockResolvedValue(stored); // what the server returns after the turn
    await openAndType("go");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("called 1__echo")).toBeInTheDocument();
    expect(screen.getByText("echoed")).toBeInTheDocument();
    expect(screen.getByText("all done")).toBeInTheDocument();
    expect(screen.getByText("ok")).toBeInTheDocument(); // the tool result's status tag
  });
});
