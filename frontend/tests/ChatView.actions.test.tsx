import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatView } from "../src/ChatView";
import type { ChatMessageItem, ChatSummary } from "../src/api";

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
vi.mock("../src/clipboard-utils", () => ({ copyToClipboard: vi.fn().mockResolvedValue(true) }));
import { api } from "../src/api";
import { copyToClipboard } from "../src/clipboard-utils";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const chat: ChatSummary = { id: 1, runtime_id: 1, agent_id: null, title: "t", project_id: null, created_at: "x", updated_at: "x" };
const msg = (id: number, role: ChatMessageItem["role"], content: string): ChatMessageItem => ({
  id, chat_id: 1, role, content, prompt_tokens: null, completion_tokens: null, latency_ms: null, created_at: "t", tool_meta: {},
});
const HISTORY = [msg(1, "user", "first question"), msg(2, "assistant", "first answer"), msg(3, "user", "second"), msg(4, "assistant", "second answer")];

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Object.values(m).forEach((f) => f.mockReset());
  vi.mocked(copyToClipboard).mockClear();
  m.listChats.mockResolvedValue([chat]);
  m.listRuntimes.mockResolvedValue([{ id: 1, name: "rt" }, { id: 2, name: "other-rt" }]);
  m.listAgents.mockResolvedValue([{ id: 7, name: "Helper", agent_backend: "generic" }]);
  m.listAgentBackends.mockResolvedValue([]);
  m.getChatMessages.mockResolvedValue(HISTORY);
  m.getChatContext.mockRejectedValue(new Error("not needed here"));
  m.listMcpServers.mockResolvedValue([]);
  m.streamChatMessage.mockResolvedValue(undefined);
});

describe("message actions", () => {
  it("Copy puts that message's text on the clipboard", async () => {
    render(<ChatView />);
    await screen.findByText("first answer");
    await userEvent.click(screen.getAllByRole("button", { name: "Copy message" })[1]);
    expect(copyToClipboard).toHaveBeenCalledWith("first answer");
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("Edit is offered on your messages only; saving resends from that message", async () => {
    render(<ChatView />);
    await screen.findByText("first answer");
    expect(screen.getAllByRole("button", { name: "Edit message" })).toHaveLength(2); // the two user messages
    await userEvent.click(screen.getAllByRole("button", { name: "Edit message" })[0]);
    const box = screen.getByLabelText("Edit message", { selector: "textarea" });
    expect(box).toHaveValue("first question");
    await userEvent.clear(box);
    await userEvent.type(box, "reworded");
    await userEvent.click(screen.getByRole("button", { name: "Save and resend" }));
    await waitFor(() => expect(m.streamChatMessage).toHaveBeenCalled());
    const [chatId, content, , opts] = m.streamChatMessage.mock.calls[0];
    expect(chatId).toBe(1);
    expect(content).toBe("reworded");
    expect(opts).toEqual(expect.objectContaining({ replace_from_message_id: 1 }));
  });

  it("Cancel leaves the message alone and sends nothing", async () => {
    render(<ChatView />);
    await screen.findByText("first answer");
    await userEvent.click(screen.getAllByRole("button", { name: "Edit message" })[0]);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("first question")).toBeInTheDocument();
    expect(m.streamChatMessage).not.toHaveBeenCalled();
  });

  it("a refused edit keeps what you typed", async () => {
    m.streamChatMessage.mockRejectedValue(new Error("runtime is not online"));
    render(<ChatView />);
    await screen.findByText("first answer");
    await userEvent.click(screen.getAllByRole("button", { name: "Edit message" })[0]);
    const box = screen.getByLabelText("Edit message", { selector: "textarea" });
    await userEvent.type(box, " more");
    await userEvent.click(screen.getByRole("button", { name: "Save and resend" }));
    expect(await screen.findByLabelText("Edit message", { selector: "textarea" })).toHaveValue("first question more");
  });

  it("Retry is offered on replies only and asks again with no new text", async () => {
    render(<ChatView />);
    await screen.findByText("first answer");
    expect(screen.getAllByRole("button", { name: "Retry reply" })).toHaveLength(2);
    await userEvent.click(screen.getAllByRole("button", { name: "Retry reply" })[1]);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(m.streamChatMessage).toHaveBeenCalled());
    const [, content, , opts] = m.streamChatMessage.mock.calls[0];
    expect(content).toBe("");
    expect(opts).toEqual(expect.objectContaining({ replace_from_message_id: 4 }));
    expect(opts.runtime_id).toBeUndefined(); // same LLM and agent: nothing is switched
    expect(opts.agent_id).toBeUndefined();
    expect(opts.clear_agent).toBeUndefined();
  });

  it("Retry with another LLM and an agent sends exactly those", async () => {
    render(<ChatView />);
    await screen.findByText("first answer");
    await userEvent.click(screen.getAllByRole("button", { name: "Retry reply" })[1]);
    await userEvent.selectOptions(screen.getByLabelText("Retry with LLM"), "2");
    await userEvent.selectOptions(screen.getByLabelText("Retry with agent"), "7");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(m.streamChatMessage).toHaveBeenCalled());
    expect(m.streamChatMessage.mock.calls[0][3]).toEqual(expect.objectContaining({ replace_from_message_id: 4, runtime_id: 2, agent_id: 7 }));
  });

  it("while a reply is being generated, Edit and Retry are disabled", async () => {
    m.streamChatMessage.mockImplementation(() => new Promise<void>(() => {}));
    render(<ChatView />);
    const box = await screen.findByPlaceholderText("Message this LLM…");
    await userEvent.type(box, "go");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByTestId("live-reply");
    for (const b of screen.getAllByRole("button", { name: "Edit message" })) expect(b).toBeDisabled();
    for (const b of screen.getAllByRole("button", { name: "Retry reply" })) expect(b).toBeDisabled();
  });
});
