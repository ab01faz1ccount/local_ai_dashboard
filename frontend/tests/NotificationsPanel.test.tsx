import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NotificationsPanel } from "../src/components/NotificationsPanel";
import type { LogEvent } from "../src/api";

let handler: ((ev: LogEvent) => void) | null = null;
vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  return {
    ...actual,
    connectEventsSocket: (cb: (ev: LogEvent) => void) => {
      handler = cb;
      return () => {
        handler = null;
      };
    },
  };
});

const evt = (event_type: string, metadata: Record<string, unknown> = {}, event_id = `id-${event_type}-${Math.random()}`): LogEvent =>
  ({ id: 1, event_id, event_type, timestamp: "2026-01-01T12:00:00Z", device_id: null, runtime_id: null, agent_id: null, session_id: null,
     source: { system: "local", project_id: null, agent_id: null, session_id: null, task_id: null }, metadata }) as unknown as LogEvent;

const push = (ev: LogEvent) => act(() => handler!(ev));

beforeEach(() => {
  handler = null;
});

describe("NotificationsPanel", () => {
  it("starts with no badge and an empty dropdown message", async () => {
    render(<NotificationsPanel />);
    expect(screen.queryByLabelText(/unread/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Notifications" }));
    expect(screen.getByText("Nothing needs your attention.")).toBeInTheDocument();
  });

  it("an important event raises the unread badge", () => {
    render(<NotificationsPanel />);
    push(evt("runtime.crashed", { error: "oom" }));
    expect(screen.getByLabelText("1 unread")).toBeInTheDocument();
  });

  it("an unimportant event does nothing", () => {
    render(<NotificationsPanel />);
    push(evt("inference.completed"));
    push(evt("runtime.metrics"));
    expect(screen.queryByLabelText(/unread/)).not.toBeInTheDocument();
  });

  it("the badge counts each distinct event exactly once", () => {
    render(<NotificationsPanel />);
    push(evt("runtime.crashed"));
    push(evt("permission.requested", { description: "run it" }));
    expect(screen.getByLabelText("2 unread")).toBeInTheDocument();
  });

  it("a redelivered event (same id) is not double-counted", () => {
    render(<NotificationsPanel />);
    const e = evt("runtime.crashed", {}, "same");
    push(e);
    push(e);
    expect(screen.getByLabelText("1 unread")).toBeInTheDocument();
  });

  it("opening the dropdown clears the badge and lists newest first", async () => {
    render(<NotificationsPanel />);
    push(evt("runtime.crashed", { error: "first" }));
    push(evt("mcp.connect_failed", { name: "files", error: "second" }));
    await userEvent.click(screen.getByRole("button", { name: "Notifications" }));
    expect(screen.queryByLabelText(/unread/)).not.toBeInTheDocument();
    const items = screen.getAllByTestId("notification");
    expect(items[0]).toHaveTextContent("Could not connect to files");
    expect(items[1]).toHaveTextContent("A runtime crashed");
    expect(items[1]).toHaveTextContent("first");
  });

  it("an event arriving while open still counts as unread", async () => {
    render(<NotificationsPanel />);
    await userEvent.click(screen.getByRole("button", { name: "Notifications" }));
    push(evt("runtime.crashed"));
    expect(screen.getByLabelText("1 unread")).toBeInTheDocument();
  });

  it("Clear all empties the list and badge", async () => {
    render(<NotificationsPanel />);
    push(evt("runtime.crashed"));
    await userEvent.click(screen.getByRole("button", { name: "Notifications" }));
    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(screen.getByText("Nothing needs your attention.")).toBeInTheDocument();
  });

  it("a requested MCP disconnect is silent; a lost one is not", () => {
    render(<NotificationsPanel />);
    push(evt("mcp.disconnected", { reason: "requested", name: "files" }));
    expect(screen.queryByLabelText(/unread/)).not.toBeInTheDocument();
    push(evt("mcp.disconnected", { reason: "lost", name: "files" }));
    expect(screen.getByLabelText("1 unread")).toBeInTheDocument();
  });

  it("closes the socket on unmount", () => {
    const { unmount } = render(<NotificationsPanel />);
    expect(handler).not.toBeNull();
    unmount();
    expect(handler).toBeNull();
  });
});
