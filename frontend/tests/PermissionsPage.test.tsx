import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PermissionsPage } from "../src/PermissionsPage";
import type { PermissionGrant } from "../src/api";

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  return { ...actual, api: { listPermissionGrants: vi.fn(), revokePermissionGrant: vi.fn() } };
});
import { api } from "../src/api";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const grant = (o: Partial<PermissionGrant> = {}): PermissionGrant => ({
  id: 1, scope_type: "mcp_server", scope_key: "5", risk_level: "HIGH", decision: "allow_always", description: null,
  agent_id: null, session_id: null, granted_at: "2026-01-01T00:00:00Z", expires_at: null, active: true, ...o,
});

beforeEach(() => Object.values(m).forEach((f) => f.mockReset()));

describe("PermissionsPage", () => {
  it("loads active grants by default", async () => {
    m.listPermissionGrants.mockResolvedValue([grant()]);
    render(<PermissionsPage />);
    expect(await screen.findByTestId("grant-1")).toBeInTheDocument();
    expect(m.listPermissionGrants).toHaveBeenCalledWith({ activeOnly: true, scopeType: undefined });
    expect(within(screen.getByTestId("grant-1")).getByText("Active")).toBeInTheDocument();
  });

  it("empty state message adapts to the active-only filter", async () => {
    m.listPermissionGrants.mockResolvedValue([]);
    render(<PermissionsPage />);
    expect(await screen.findByText("No active permission grants.")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Active only"));
    expect(await screen.findByText("No permission grants.")).toBeInTheDocument();
    expect(m.listPermissionGrants).toHaveBeenLastCalledWith({ activeOnly: false, scopeType: undefined });
  });

  it("scope type filter is passed through", async () => {
    m.listPermissionGrants.mockResolvedValue([]);
    render(<PermissionsPage />);
    await screen.findByText(/No .*permission grants\./);
    await userEvent.selectOptions(screen.getByRole("combobox"), "mcp_tool");
    await waitFor(() => expect(m.listPermissionGrants).toHaveBeenLastCalledWith({ activeOnly: true, scopeType: "mcp_tool" }));
  });

  it("revoke calls the API and reloads", async () => {
    m.listPermissionGrants.mockResolvedValueOnce([grant()]).mockResolvedValueOnce([grant({ active: false, expires_at: "2026-01-02T00:00:00Z" })]);
    m.revokePermissionGrant.mockResolvedValue(grant({ active: false }));
    render(<PermissionsPage />);
    await screen.findByTestId("grant-1");
    await userEvent.click(within(screen.getByTestId("grant-1")).getByRole("button", { name: "Revoke" }));
    expect(m.revokePermissionGrant).toHaveBeenCalledWith(1);
    await waitFor(() => expect(m.listPermissionGrants).toHaveBeenCalledTimes(2));
  });

  it("an ended grant has no revoke button", async () => {
    m.listPermissionGrants.mockResolvedValue([grant({ active: false, decision: "deny", expires_at: "2026-01-01T00:00:00Z" })]);
    render(<PermissionsPage />);
    const row = await screen.findByTestId("grant-1");
    expect(within(row).queryByRole("button", { name: "Revoke" })).not.toBeInTheDocument();
    expect(within(row).getByText("Ended")).toBeInTheDocument();
  });

  it("a load error is shown", async () => {
    m.listPermissionGrants.mockRejectedValue(new Error(JSON.stringify({ detail: "backend down" })));
    render(<PermissionsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("backend down");
  });

  it("a revoke error is shown without losing the list", async () => {
    m.listPermissionGrants.mockResolvedValue([grant()]);
    m.revokePermissionGrant.mockRejectedValue(new Error(JSON.stringify({ detail: "already gone" })));
    render(<PermissionsPage />);
    await screen.findByTestId("grant-1");
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already gone");
    expect(screen.getByTestId("grant-1")).toBeInTheDocument();
  });
});
