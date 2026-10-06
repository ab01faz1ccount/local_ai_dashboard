import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContextInspectorPanel } from "../src/components/ContextInspectorPanel";
import type { ContextInspection } from "../src/api";

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  return { ...actual, api: { getChatContext: vi.fn() } };
});
import { api } from "../src/api";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const data = (o: Partial<ContextInspection> = {}): ContextInspection => ({
  chat_id: 1, runtime_id: 1, context_window: 4096, context_window_source: "runtime_config", method: "tokenizer",
  categories: [
    { key: "system", label: "System prompt", tokens: 200, items: 1, percent_of_window: 4.9 },
    { key: "tool_definitions", label: "Tool definitions", tokens: 800, items: 6, percent_of_window: 19.5 },
    { key: "conversation", label: "Conversation", tokens: 600, items: 4, percent_of_window: 14.6 },
    { key: "tool_results", label: "Tool results", tokens: 1400, items: 1, percent_of_window: 34.2 },
  ],
  total_tokens: 3000, percent_used: 73.2, headroom_tokens: 1096, status: "OK", last_prompt_tokens: 3050, tool_count: 6,
  largest_items: [{ message_id: 9, role: "tool", category: "tool_results", chars: 5000, estimated_tokens: 1250, preview: "file contents…" }],
  ...o,
});

beforeEach(() => {
  m.getChatContext.mockReset();
  m.getChatContext.mockResolvedValue(data());
});

describe("ContextInspectorPanel", () => {
  it("shows a one-line summary even while collapsed", async () => {
    render(<ContextInspectorPanel chatId={1} refreshKey={0} />);
    const summary = await screen.findByRole("button", { name: /Context/ });
    expect(summary).toHaveTextContent("3,000 / 4,096 tokens (73.2%)");
    expect(summary).toHaveTextContent("Plenty of room");
    expect(screen.queryByRole("img", { name: /Context usage/ })).not.toBeInTheDocument();
  });

  it("expands to a bar, a legend with counts, and the biggest items", async () => {
    render(<ContextInspectorPanel chatId={1} refreshKey={0} />);
    await userEvent.click(await screen.findByRole("button", { name: /Context/ }));
    expect(screen.getByRole("img", { name: /Context usage/ })).toBeInTheDocument();
    const tools = screen.getByTestId("context-tool_definitions");
    expect(tools).toHaveTextContent("800");
    expect(tools).toHaveTextContent("6 tools");
    expect(screen.getByTestId("context-conversation")).toHaveTextContent("4 items");
    expect(screen.getByTestId("context-system")).toHaveTextContent("1 item");
    expect(screen.getByTestId("context-system")).not.toHaveTextContent("1 items");
    expect(screen.getByText("file contents…")).toBeInTheDocument();
    expect(screen.getByText("~1,250")).toBeInTheDocument();
  });

  it("says where the counts and the window came from, and notes the previous request", async () => {
    render(<ContextInspectorPanel chatId={1} refreshKey={0} />);
    await userEvent.click(await screen.findByRole("button", { name: /Context/ }));
    expect(screen.getByText(/Counted with the runtime's own tokenizer/)).toBeInTheDocument();
    expect(screen.getByText(/configured context size/)).toBeInTheDocument();
    expect(screen.getByText(/1,096 tokens of headroom/)).toBeInTheDocument();
    expect(screen.getByText(/Previous request used 3,050/)).toBeInTheDocument();
  });

  it("is honest that an estimate is an estimate", async () => {
    m.getChatContext.mockResolvedValue(data({ method: "estimate", context_window_source: "model_metadata" }));
    render(<ContextInspectorPanel chatId={1} refreshKey={0} />);
    await userEvent.click(await screen.findByRole("button", { name: /Context/ }));
    expect(screen.getByText(/Estimated from text length/)).toBeInTheDocument();
    expect(screen.getByText(/trained context length/)).toBeInTheDocument();
  });

  it("over the window: says by how much and flags it", async () => {
    m.getChatContext.mockResolvedValue(data({ total_tokens: 5000, percent_used: 122.1, headroom_tokens: -904, status: "OVER" }));
    render(<ContextInspectorPanel chatId={1} refreshKey={0} />);
    const summary = await screen.findByRole("button", { name: /Context/ });
    expect(within(summary).getByText("Over the limit")).toHaveAttribute("data-tone", "bad");
    await userEvent.click(summary);
    expect(screen.getByText(/Over by 904 tokens/)).toBeInTheDocument();
  });

  it("unknown window: shows the total without a denominator or percentage", async () => {
    m.getChatContext.mockResolvedValue(data({ context_window: null, context_window_source: null, percent_used: null, headroom_tokens: null, status: "UNKNOWN" }));
    render(<ContextInspectorPanel chatId={1} refreshKey={0} />);
    const summary = await screen.findByRole("button", { name: /Context/ });
    expect(summary).toHaveTextContent("3,000 tokens");
    expect(summary).not.toHaveTextContent("/");
    expect(summary).not.toHaveTextContent("%");
    expect(summary).toHaveTextContent("Window size unknown");
  });

  it("an empty conversation says so instead of drawing an empty bar", async () => {
    m.getChatContext.mockResolvedValue(data({
      total_tokens: 0, percent_used: 0, headroom_tokens: 4096, last_prompt_tokens: null, largest_items: [],
      categories: data().categories.map((c) => ({ ...c, tokens: 0, percent_of_window: 0 })),
    }));
    render(<ContextInspectorPanel chatId={1} refreshKey={0} />);
    await userEvent.click(await screen.findByRole("button", { name: /Context/ }));
    expect(screen.getByText("Nothing in the context yet.")).toBeInTheDocument();
    expect(screen.queryByText("LARGEST ITEMS")).not.toBeInTheDocument();
  });

  it("refetches when the conversation changes (refreshKey) and for a different chat", async () => {
    const { rerender } = render(<ContextInspectorPanel chatId={1} refreshKey={0} />);
    await waitFor(() => expect(m.getChatContext).toHaveBeenCalledTimes(1));
    rerender(<ContextInspectorPanel chatId={1} refreshKey={1} />);
    await waitFor(() => expect(m.getChatContext).toHaveBeenCalledTimes(2));
    rerender(<ContextInspectorPanel chatId={2} refreshKey={1} />);
    await waitFor(() => expect(m.getChatContext).toHaveBeenLastCalledWith(2));
  });

  it("an API error is shown, not swallowed", async () => {
    m.getChatContext.mockRejectedValue(new Error(JSON.stringify({ detail: "chat not found" })));
    render(<ContextInspectorPanel chatId={1} refreshKey={0} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("chat not found");
  });
});
