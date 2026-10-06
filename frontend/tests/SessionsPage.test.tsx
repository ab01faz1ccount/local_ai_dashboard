import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SessionsPage } from "../src/SessionsPage";
import type { SessionDetail, SessionSummary } from "../src/api";

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  return { ...actual, api: { listSessions: vi.fn(), getSession: vi.fn(), exportSession: vi.fn() } };
});
vi.mock("../src/session-utils", async (orig) => {
  const actual = await orig<typeof import("../src/session-utils")>();
  return { ...actual, downloadTextFile: vi.fn() };
});
import { api } from "../src/api";
import { downloadTextFile } from "../src/session-utils";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const session = (o: Partial<SessionSummary> = {}): SessionSummary => ({
  id: 1, runtime_id: 1, runtime_name: "llama-a", agent_id: 1, agent_name: "hermes", started_at: "2026-01-01T00:00:00Z",
  ended_at: "2026-01-01T00:10:00Z", status: "COMPLETED", requests_count: 4, prompt_tokens_total: 100,
  completion_tokens_total: 50, avg_latency_ms: 321, avg_tokens_per_sec: 18.5, ...o,
});
const detail = (o: Partial<SessionDetail> = {}): SessionDetail => ({
  ...session(), chat_ids: [7, 9], event_counts: { "tool.called": 3, "tool.completed": 2, "tool.failed": 1 }, ...o,
});

beforeEach(() => {
  Object.values(m).forEach((f) => f.mockReset());
  (downloadTextFile as unknown as ReturnType<typeof vi.fn>).mockReset();
  m.listSessions.mockResolvedValue([session()]);
  m.getSession.mockResolvedValue(detail());
});

describe("SessionsPage", () => {
  it("lists sessions with names, status, duration and token totals", async () => {
    render(<SessionsPage />);
    const row = await screen.findByTestId("session-1");
    expect(row).toHaveTextContent("#1 · hermes → llama-a");
    expect(row).toHaveTextContent("COMPLETED");
    expect(row).toHaveTextContent("10m 0s");
    expect(row).toHaveTextContent("4 req · 150 tok");
  });

  it("falls back to ids when names are unknown", async () => {
    m.listSessions.mockResolvedValue([session({ agent_name: null, runtime_name: null, agent_id: 5, runtime_id: 6 })]);
    render(<SessionsPage />);
    expect(await screen.findByTestId("session-1")).toHaveTextContent("agent 5 → runtime 6");
  });

  it("empty state adapts to the active-only filter", async () => {
    m.listSessions.mockResolvedValue([]);
    render(<SessionsPage />);
    expect(await screen.findByText(/No sessions yet/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Active only"));
    expect(await screen.findByText(/No active sessions yet/)).toBeInTheDocument();
    expect(m.listSessions).toHaveBeenLastCalledWith({ activeOnly: true });
  });

  it("selecting a session shows its stats, tool-call summary and chats", async () => {
    render(<SessionsPage />);
    await userEvent.click(await screen.findByTestId("session-1"));
    const panel = await screen.findByLabelText("Session 1 detail");
    expect(m.getSession).toHaveBeenCalledWith(1);
    expect(within(panel).getByText("321 ms")).toBeInTheDocument();
    expect(within(panel).getByText("18.5 t/s")).toBeInTheDocument();
    expect(within(panel).getByTestId("tool-call-summary")).toHaveTextContent("3 made · 2 completed · 1 failed");
    expect(panel).toHaveTextContent("Chats: #7, #9");
  });

  it("says so when no chats were recorded", async () => {
    m.getSession.mockResolvedValue(detail({ chat_ids: [] }));
    render(<SessionsPage />);
    await userEvent.click(await screen.findByTestId("session-1"));
    expect(await screen.findByText(/none recorded/)).toBeInTheDocument();
  });

  it("clicking the selected session again closes the detail", async () => {
    render(<SessionsPage />);
    await userEvent.click(await screen.findByTestId("session-1"));
    await screen.findByLabelText("Session 1 detail");
    await userEvent.click(screen.getByTestId("session-1"));
    await waitFor(() => expect(screen.queryByLabelText("Session 1 detail")).not.toBeInTheDocument());
  });

  it.each([["Export JSON", "json"], ["Export JSONL", "jsonl"], ["Export Markdown", "markdown"]])(
    "%s fetches %s and triggers a download of what the server sent",
    async (label, format) => {
      m.exportSession.mockResolvedValue({ filename: `session-1.${format}`, content: "BODY" });
      render(<SessionsPage />);
      await userEvent.click(await screen.findByTestId("session-1"));
      await userEvent.click(await screen.findByRole("button", { name: label }));
      expect(m.exportSession).toHaveBeenCalledWith(1, format);
      await waitFor(() => expect(downloadTextFile).toHaveBeenCalledWith(`session-1.${format}`, "BODY"));
    }
  );

  it("an export failure is shown and nothing is downloaded", async () => {
    m.exportSession.mockRejectedValue(new Error(JSON.stringify({ detail: "export blew up" })));
    render(<SessionsPage />);
    await userEvent.click(await screen.findByTestId("session-1"));
    await userEvent.click(await screen.findByRole("button", { name: "Export JSON" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("export blew up");
    expect(downloadTextFile).not.toHaveBeenCalled();
  });

  it("a list load error is shown", async () => {
    m.listSessions.mockRejectedValue(new Error(JSON.stringify({ detail: "backend down" })));
    render(<SessionsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("backend down");
  });
});
