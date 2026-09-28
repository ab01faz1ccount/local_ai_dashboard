import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LogsPage } from "../src/LogsPage";
import type { AgentSummary, EventTypeInfo, LogEvent, RuntimeSummary } from "../src/api";

let socketHandler: ((ev: LogEvent) => void) | null = null;
const socketCleanup = vi.fn();

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  return {
    ...actual,
    api: {
      getEventTypes: vi.fn(),
      listRuntimes: vi.fn(),
      listAgents: vi.fn(),
      listEvents: vi.fn(),
    },
    connectEventsSocket: vi.fn((onEvent: (ev: LogEvent) => void) => {
      socketHandler = onEvent;
      return socketCleanup;
    }),
  };
});
import { api } from "../src/api";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

/** Scopes queries to the events table -- event-type strings like
 * "runtime.started" also appear as <option> text in the filter dropdown,
 * so an unscoped screen.getByText would be ambiguous once a table exists. */
async function table() {
  return within(await screen.findByRole("table"));
}

const TYPES: EventTypeInfo[] = [
  { event_type: "runtime.started", description: "A runtime came online." },
  { event_type: "runtime.crashed", description: "A runtime ended up in error." },
  { event_type: "model.loaded", description: "A model was loaded." },
  { event_type: "agent.created", description: "An agent was created." },
  { event_type: "tool.called", description: "(reserved) A tool call started." },
];
const RUNTIMES: RuntimeSummary[] = [
  {
    id: 1,
    name: "LLM1",
    engine_type: "llama.cpp",
    model_id: null,
    model_name: null,
    host: "127.0.0.1",
    port: 8080,
    executable_path: null,
    status: "ONLINE",
    pid: null,
    last_error: null,
    config_json: {},
    source_system: "local",
  },
];
const AGENTS: AgentSummary[] = [
  {
    id: 5,
    name: "Agent5",
    description: null,
    agent_type: "generic",
    agent_backend: "generic",
    default_runtime_id: null,
    config_json: {},
    status: "INACTIVE",
  },
];

function ev(over: Partial<LogEvent> = {}): LogEvent {
  return {
    id: 1,
    event_id: "e1",
    event_type: "runtime.started",
    timestamp: "2026-01-01T10:00:00.000Z",
    device_id: "dev1",
    runtime_id: 1,
    agent_id: null,
    session_id: null,
    source: { system: "local", project_id: null, agent_id: null, session_id: null, task_id: null },
    metadata: {},
    ...over,
  };
}

beforeEach(() => {
  Object.values(m).forEach((f) => f.mockReset());
  socketHandler = null;
  socketCleanup.mockClear();
  m.getEventTypes.mockResolvedValue(TYPES);
  m.listRuntimes.mockResolvedValue(RUNTIMES);
  m.listAgents.mockResolvedValue(AGENTS);
  m.listEvents.mockResolvedValue([]);
});

describe("LogsPage", () => {
  it("loads and renders events with resolved runtime/agent names", async () => {
    m.listEvents.mockResolvedValue([
      ev({ id: 2, event_id: "e2", event_type: "agent.created", runtime_id: null, agent_id: 5, metadata: { name: "Agent5" } }),
      ev({ id: 1, event_id: "e1", runtime_id: 1 }),
    ]);
    render(<LogsPage />);
    const scoped = await table();
    await scoped.findByText("agent.created");
    expect(scoped.getByText("runtime.started")).toBeInTheDocument();
    expect(scoped.getByText("LLM1")).toBeInTheDocument(); // resolved from runtime_id via listRuntimes
    expect(scoped.getByText("Agent5")).toBeInTheDocument(); // resolved from agent_id via listAgents
  });

  it("shows an empty state when there are no events", async () => {
    render(<LogsPage />);
    expect(await screen.findByText(/No events yet/)).toBeInTheDocument();
  });

  it("shows a readable error if loading fails", async () => {
    m.listEvents.mockRejectedValue(new Error(JSON.stringify({ detail: "backend unreachable" })));
    render(<LogsPage />);
    expect(await screen.findByText("backend unreachable")).toBeInTheDocument();
  });

  it("falls back to metadata.name when runtime_id is null (e.g. the runtime was deleted)", async () => {
    m.listEvents.mockResolvedValue([ev({ event_type: "runtime.removed", runtime_id: null, metadata: { name: "OldRuntime" } })]);
    render(<LogsPage />);
    expect(await (await table()).findByText("OldRuntime")).toBeInTheDocument();
  });

  it("does not leak an agent's fallback name into the Runtime column for a non-runtime event", async () => {
    // agent.created has no runtime_id (it was never about a runtime), even
    // though its metadata.name happens to be set -- that name belongs in
    // the Agent column's fallback, not the Runtime column's.
    m.listEvents.mockResolvedValue([ev({ event_type: "agent.created", runtime_id: null, agent_id: null, metadata: { name: "Agent5" } })]);
    render(<LogsPage />);
    const scoped = await table();
    await scoped.findByText("agent.created");
    const cells = scoped.getAllByRole("cell");
    expect(cells[2]).toHaveTextContent("—"); // Runtime column: no fallback for a non-runtime.* event
    expect(cells[3]).toHaveTextContent("Agent5"); // Agent column: correctly falls back
  });

  it("renders metadata as a compact key=value line", async () => {
    m.listEvents.mockResolvedValue([
      ev({ event_type: "inference.completed", metadata: { chat_id: 3, prompt_tokens: 10, completion_tokens: 5 } }),
    ]);
    render(<LogsPage />);
    expect(await (await table()).findByText("chat_id=3 · prompt_tokens=10 · completion_tokens=5")).toBeInTheDocument();
  });

  it("selecting a type filter reloads with that filter and clear button appears", async () => {
    render(<LogsPage />);
    await waitFor(() => expect(m.listEvents).toHaveBeenCalledTimes(1));
    await userEvent.selectOptions(screen.getByDisplayValue("All event types"), "runtime.started");
    await waitFor(() =>
      expect(m.listEvents).toHaveBeenLastCalledWith(expect.objectContaining({ eventType: "runtime.started" }))
    );
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(m.listEvents).toHaveBeenLastCalledWith(expect.objectContaining({ eventType: undefined })));
    expect(screen.queryByRole("button", { name: "Clear filters" })).toBeNull();
  });

  it("selecting a runtime filter reloads with runtimeId", async () => {
    render(<LogsPage />);
    await waitFor(() => expect(m.listEvents).toHaveBeenCalledTimes(1));
    await userEvent.selectOptions(screen.getByDisplayValue("All runtimes"), "LLM1");
    await waitFor(() => expect(m.listEvents).toHaveBeenLastCalledWith(expect.objectContaining({ runtimeId: 1 })));
  });

  it("Load older fetches the next page with before_id and appends", async () => {
    m.listEvents.mockResolvedValueOnce(Array.from({ length: 50 }, (_, i) => ev({ id: 100 - i, event_id: `p1-${i}` })));
    render(<LogsPage />);
    const loadOlder = await screen.findByRole("button", { name: "Load older" });

    m.listEvents.mockResolvedValueOnce([ev({ id: 40, event_id: "older" })]);
    await userEvent.click(loadOlder);
    expect(m.listEvents).toHaveBeenLastCalledWith(expect.objectContaining({ beforeId: 51 }));
    const scoped = await table();
    await waitFor(() => expect(scoped.getAllByText("runtime.started")).toHaveLength(51));
  });

  it("hides Load older once a short page comes back", async () => {
    m.listEvents.mockResolvedValue([ev()]);
    render(<LogsPage />);
    await (await table()).findByText("runtime.started");
    expect(screen.queryByRole("button", { name: "Load older" })).toBeNull();
  });

  it("subscribes to the live socket and prepends new matching events", async () => {
    m.listEvents.mockResolvedValue([ev({ id: 1, event_id: "old" })]);
    render(<LogsPage />);
    await (await table()).findByText("runtime.started");
    expect(socketHandler).not.toBeNull();

    act(() => socketHandler!(ev({ id: 2, event_id: "new", event_type: "runtime.crashed" })));
    await (await table()).findByText("runtime.crashed");
    const rows = screen.getAllByRole("row").slice(1); // drop header row
    expect(within(rows[0]).getByText("runtime.crashed")).toBeInTheDocument(); // newest on top
  });

  it("pausing stops new live events from being added; resuming lets them back in", async () => {
    m.listEvents.mockResolvedValue([ev({ id: 1, event_id: "old" })]);
    render(<LogsPage />);
    await (await table()).findByText("runtime.started");

    await userEvent.click(screen.getByRole("button", { name: "⏸ Pause" }));
    act(() => socketHandler!(ev({ id: 2, event_id: "while-paused", event_type: "runtime.crashed" })));
    await new Promise((r) => setTimeout(r, 20));
    expect((await table()).queryByText("runtime.crashed")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "▶ Resume" }));
    act(() => socketHandler!(ev({ id: 3, event_id: "after-resume", event_type: "model.loaded" })));
    expect(await (await table()).findByText("model.loaded")).toBeInTheDocument();
  });

  it("a live event that doesn't match the active runtime filter is ignored", async () => {
    m.listEvents.mockResolvedValue([ev({ id: 1, event_id: "seed", runtime_id: 1 })]);
    render(<LogsPage />);
    await (await table()).findByText("runtime.started");
    await userEvent.selectOptions(screen.getByDisplayValue("All runtimes"), "LLM1");
    await waitFor(() => expect(m.listEvents).toHaveBeenLastCalledWith(expect.objectContaining({ runtimeId: 1 })));

    socketHandler!(ev({ id: 9, event_id: "other-runtime", runtime_id: 2, event_type: "runtime.crashed" }));
    await new Promise((r) => setTimeout(r, 20));
    const scoped = await table();
    expect(scoped.queryByText("runtime.crashed")).toBeNull();
    expect(scoped.getAllByRole("row")).toHaveLength(2); // header + the one seeded row, nothing added
  });

  it("disconnects the socket on unmount", async () => {
    const { unmount } = render(<LogsPage />);
    await waitFor(() => expect(m.listEvents).toHaveBeenCalledTimes(1));
    unmount();
    expect(socketCleanup).toHaveBeenCalled();
  });
});
