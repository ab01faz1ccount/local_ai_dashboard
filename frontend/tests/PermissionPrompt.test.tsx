import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PermissionPrompt } from "../src/components/PermissionPrompt";
import type { LogEvent, PendingPermissionRequest } from "../src/api";

let socketHandler: ((ev: LogEvent) => void) | null = null;

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  return {
    ...actual,
    connectEventsSocket: (cb: (ev: LogEvent) => void) => {
      socketHandler = cb;
      return () => {
        socketHandler = null;
      };
    },
    api: { listPendingPermissions: vi.fn(), resolvePermissionRequest: vi.fn() },
  };
});
import { api } from "../src/api";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const pend = (o: Partial<PendingPermissionRequest> = {}): PendingPermissionRequest => ({
  id: "r1", scope_type: "mcp_tool", scope_key: "3:delete_file", risk_level: "CRITICAL", session_id: null, agent_id: null,
  description: "delete a file", requested_at: "2026-01-01T00:00:00Z", ...o,
});
const evt = (type: string, metadata: Record<string, unknown> = {}): LogEvent => ({
  id: 1, event_id: "e1", event_type: type, timestamp: "2026-01-01T00:00:00Z", device_id: null, runtime_id: null,
  agent_id: null, session_id: null, source: { system: "local", project_id: null, agent_id: null, session_id: null, task_id: null } as never,
  metadata,
});

beforeEach(() => {
  Object.values(m).forEach((f) => f.mockReset());
  m.listPendingPermissions.mockResolvedValue([]);
  socketHandler = null;
});

describe("PermissionPrompt", () => {
  it("renders nothing when there's nothing pending", async () => {
    render(<PermissionPrompt />);
    await waitFor(() => expect(m.listPendingPermissions).toHaveBeenCalled());
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("shows a request already pending on mount (GET /pending)", async () => {
    m.listPendingPermissions.mockResolvedValue([pend()]);
    render(<PermissionPrompt />);
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByText("delete_file (server #3)", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("delete a file")).toBeInTheDocument();
    expect(screen.getByText("Critical risk")).toBeInTheDocument();
  });

  it("a live permission.requested event triggers a re-fetch and shows the modal", async () => {
    render(<PermissionPrompt />);
    await waitFor(() => expect(m.listPendingPermissions).toHaveBeenCalledTimes(1));
    m.listPendingPermissions.mockResolvedValue([pend({ id: "r2" })]);
    socketHandler?.(evt("permission.requested"));
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
  });

  it("Allow once resolves and closes the modal", async () => {
    m.listPendingPermissions.mockResolvedValue([pend()]);
    m.resolvePermissionRequest.mockResolvedValue({ resolved: true });
    render(<PermissionPrompt />);
    await screen.findByRole("alertdialog");
    await userEvent.click(screen.getByRole("button", { name: "Allow once" }));
    expect(m.resolvePermissionRequest).toHaveBeenCalledWith("r1", "allow_once");
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it.each([
    ["Allow for session", "allow_session"],
    ["Always allow", "allow_always"],
    ["Deny", "deny"],
  ])("%s sends %s", async (label, decision) => {
    m.listPendingPermissions.mockResolvedValue([pend()]);
    m.resolvePermissionRequest.mockResolvedValue({ resolved: true });
    render(<PermissionPrompt />);
    await screen.findByRole("alertdialog");
    await userEvent.click(screen.getByRole("button", { name: label }));
    expect(m.resolvePermissionRequest).toHaveBeenCalledWith("r1", decision);
  });

  it("a resolve failure keeps the modal open and shows the error", async () => {
    m.listPendingPermissions.mockResolvedValue([pend()]);
    m.resolvePermissionRequest.mockRejectedValue(new Error(JSON.stringify({ detail: "already resolved" })));
    render(<PermissionPrompt />);
    await screen.findByRole("alertdialog");
    await userEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already resolved");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });

  it("shows how many more are waiting behind the current one", async () => {
    m.listPendingPermissions.mockResolvedValue([pend({ id: "r1" }), pend({ id: "r2" }), pend({ id: "r3" })]);
    render(<PermissionPrompt />);
    expect(await screen.findByText("+2 more waiting")).toBeInTheDocument();
  });

  it("advances to the next request after the current one is resolved", async () => {
    m.listPendingPermissions.mockResolvedValue([pend({ id: "r1" }), pend({ id: "r2", scope_key: "9:read", description: "read a file" })]);
    m.resolvePermissionRequest.mockResolvedValue({ resolved: true });
    render(<PermissionPrompt />);
    await screen.findByText("delete a file");
    await userEvent.click(screen.getByRole("button", { name: "Allow once" }));
    expect(await screen.findByText("read a file")).toBeInTheDocument();
  });

  it("a permission.denied event for the current request removes it (resolved from elsewhere)", async () => {
    m.listPendingPermissions.mockResolvedValue([pend()]);
    render(<PermissionPrompt />);
    await screen.findByRole("alertdialog");
    act(() => socketHandler?.(evt("permission.denied", { request_id: "r1" })));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it("an approved/denied event for a DIFFERENT request id doesn't touch the current one", async () => {
    m.listPendingPermissions.mockResolvedValue([pend()]);
    render(<PermissionPrompt />);
    await screen.findByRole("alertdialog");
    act(() => socketHandler?.(evt("permission.approved", { request_id: "someone-else" })));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });
});
