import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { McpPage } from "../src/McpPage";
import type { McpServer } from "../src/api";

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  return {
    ...actual,
    api: {
      listMcpServers: vi.fn(), createMcpServer: vi.fn(), updateMcpServer: vi.fn(), deleteMcpServer: vi.fn(),
      connectMcpServer: vi.fn(), disconnectMcpServer: vi.fn(), listMcpServerTools: vi.fn(),
    },
  };
});
import { api } from "../src/api";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const srv = (o: Partial<McpServer> = {}): McpServer => ({
  id: 1, name: "files", description: null, transport: "stdio", command: "npx", args: ["-y", "srv"], env_keys: [], url: null,
  header_keys: [], is_remote: false, enabled: true, status: "DISCONNECTED", last_error: null, tools_count: 0, server_info: {},
  source_system: "local", project_id: null, agent_id: null, session_id: null, created_at: "", updated_at: "", ...o,
});
const card = (name: string) => screen.getByTestId(`mcp-card-${name}`);

beforeEach(() => {
  Object.values(m).forEach((f) => f.mockReset());
  m.listMcpServers.mockResolvedValue([]);
});

describe("McpPage", () => {
  it("shows an empty state with an add prompt", async () => {
    render(<McpPage />);
    expect(await screen.findByText("No MCP servers yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add your first server" }));
    expect(screen.getByRole("form", { name: "Add MCP server" })).toBeInTheDocument();
  });

  it("shows a load error", async () => {
    m.listMcpServers.mockRejectedValue(new Error(JSON.stringify({ detail: "backend down" })));
    render(<McpPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("backend down");
  });

  it("lists servers with status, transport, command and hidden-value secrets", async () => {
    m.listMcpServers.mockResolvedValue([
      srv({ env_keys: ["API_KEY"], status: "CONNECTED", tools_count: 4, server_info: { name: "fs", version: "1.2" } }),
      srv({ id: 2, name: "hosted", transport: "sse", command: null, args: [], url: "https://mcp.example.com/sse", is_remote: true, header_keys: ["Authorization"] }),
    ]);
    render(<McpPage />);
    const a = await screen.findByTestId("mcp-card-files");
    expect(within(a).getByText("CONNECTED")).toBeInTheDocument();
    expect(within(a).getByText("npx -y srv")).toBeInTheDocument();
    expect(within(a).getByText("4")).toBeInTheDocument();
    expect(within(a).getByText(/env: API_KEY/)).toBeInTheDocument();
    expect(within(a).getByText(/values hidden/)).toBeInTheDocument();
    const b = card("hosted");
    expect(within(b).getByText("remote")).toBeInTheDocument();
    expect(within(b).getByText("https://mcp.example.com/sse")).toBeInTheDocument();
    expect(within(b).getByText("—")).toBeInTheDocument(); // tools count hidden when not connected
  });

  it("connect: calls the API and updates the card from the result", async () => {
    m.listMcpServers.mockResolvedValue([srv()]);
    m.connectMcpServer.mockResolvedValue({ state: "CONNECTED", tools_count: 3, error: null, server_info: {}, server: srv({ status: "CONNECTED", tools_count: 3 }) });
    render(<McpPage />);
    await screen.findByTestId("mcp-card-files");
    await userEvent.click(within(card("files")).getByRole("button", { name: "Connect" }));
    expect(m.connectMcpServer).toHaveBeenCalledWith(1);
    await waitFor(() => expect(within(card("files")).getByText("CONNECTED")).toBeInTheDocument());
    expect(within(card("files")).getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
  });

  it("a failed connect shows the server's error on the card (not a page error)", async () => {
    m.listMcpServers.mockResolvedValue([srv()]);
    m.connectMcpServer.mockResolvedValue({ state: "ERROR", tools_count: 0, error: "boom", server_info: {}, server: srv({ status: "ERROR", last_error: "boom" }) });
    render(<McpPage />);
    await screen.findByTestId("mcp-card-files");
    await userEvent.click(within(card("files")).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(within(card("files")).getByRole("alert")).toHaveTextContent("boom"));
    expect(within(card("files")).getByRole("button", { name: "Connect" })).toBeEnabled(); // retry possible
  });

  it("disables Connect while connecting and shows progress", async () => {
    m.listMcpServers.mockResolvedValue([srv()]);
    let resolve!: (v: unknown) => void;
    m.connectMcpServer.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<McpPage />);
    await screen.findByTestId("mcp-card-files");
    await userEvent.click(within(card("files")).getByRole("button", { name: "Connect" }));
    expect(within(card("files")).getByRole("button", { name: "Connecting…" })).toBeDisabled();
    expect(within(card("files")).getByText("CONNECTING")).toBeInTheDocument();
    resolve({ state: "CONNECTED", tools_count: 0, error: null, server_info: {}, server: srv({ status: "CONNECTED" }) });
    await waitFor(() => expect(within(card("files")).getByText("CONNECTED")).toBeInTheDocument());
  });

  it("disconnect works and closes the tools panel", async () => {
    m.listMcpServers.mockResolvedValue([srv({ status: "CONNECTED", tools_count: 1 })]);
    m.listMcpServerTools.mockResolvedValue({ server_id: 1, tools: [{ name: "echo", description: "Echo text", input_schema: { properties: { text: {} } } }] });
    m.disconnectMcpServer.mockResolvedValue({ state: "DISCONNECTED", tools_count: 0, error: null, server_info: {}, server: srv() });
    render(<McpPage />);
    await screen.findByTestId("mcp-card-files");
    await userEvent.click(within(card("files")).getByRole("button", { name: "Tools" }));
    expect(await within(card("files")).findByText("echo")).toBeInTheDocument();
    expect(within(card("files")).getByText("(text)")).toBeInTheDocument();
    expect(within(card("files")).getByText("Echo text")).toBeInTheDocument();
    await userEvent.click(within(card("files")).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(within(card("files")).getByText("DISCONNECTED")).toBeInTheDocument());
    expect(within(card("files")).queryByText("echo")).not.toBeInTheDocument();
  });

  it("Tools is only available while connected; a tools error is shown in the panel", async () => {
    m.listMcpServers.mockResolvedValue([srv({ status: "CONNECTED" })]);
    m.listMcpServerTools.mockRejectedValue(new Error(JSON.stringify({ detail: "not connected" })));
    render(<McpPage />);
    await screen.findByTestId("mcp-card-files");
    await userEvent.click(within(card("files")).getByRole("button", { name: "Tools" }));
    expect(await within(card("files")).findByText("not connected")).toBeInTheDocument();
  });

  it("Tools button is disabled when not connected", async () => {
    m.listMcpServers.mockResolvedValue([srv()]);
    render(<McpPage />);
    await screen.findByTestId("mcp-card-files");
    expect(within(card("files")).getByRole("button", { name: "Tools" })).toBeDisabled();
  });

  it("enable toggle patches the server; a disabled server can't be connected", async () => {
    m.listMcpServers.mockResolvedValue([srv({ enabled: false })]);
    m.updateMcpServer.mockResolvedValue(srv({ enabled: true }));
    render(<McpPage />);
    await screen.findByTestId("mcp-card-files");
    expect(within(card("files")).getByRole("button", { name: "Connect" })).toBeDisabled();
    await userEvent.click(screen.getByLabelText("Enable files"));
    expect(m.updateMcpServer).toHaveBeenCalledWith(1, { enabled: true });
    await waitFor(() => expect(within(card("files")).getByRole("button", { name: "Connect" })).toBeEnabled());
  });

  it("delete needs an in-card confirmation", async () => {
    m.listMcpServers.mockResolvedValue([srv()]);
    m.deleteMcpServer.mockResolvedValue({ deleted: true });
    render(<McpPage />);
    await screen.findByTestId("mcp-card-files");
    await userEvent.click(within(card("files")).getByRole("button", { name: "Delete" }));
    expect(m.deleteMcpServer).not.toHaveBeenCalled();
    await userEvent.click(within(card("files")).getByRole("button", { name: "Cancel" }));
    expect(m.deleteMcpServer).not.toHaveBeenCalled();
    await userEvent.click(within(card("files")).getByRole("button", { name: "Delete" }));
    await userEvent.click(within(card("files")).getByRole("button", { name: "Yes, delete" }));
    expect(m.deleteMcpServer).toHaveBeenCalledWith(1);
    await waitFor(() => expect(screen.queryByTestId("mcp-card-files")).not.toBeInTheDocument());
  });

  it("add: builds the payload from the form and shows the new card", async () => {
    m.createMcpServer.mockResolvedValue(srv({ name: "new", env_keys: ["TOKEN"] }));
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Add your first server" }));
    await userEvent.type(screen.getByLabelText("Name"), "new");
    await userEvent.type(screen.getByLabelText(/^Command/), "npx");
    await userEvent.type(screen.getByLabelText(/^Arguments/), "-y{Enter}pkg");
    await userEvent.type(screen.getByLabelText(/^Environment variables/), "TOKEN=abc");
    await userEvent.click(screen.getByRole("button", { name: "Add server" }));
    expect(m.createMcpServer).toHaveBeenCalledWith({
      name: "new", description: null, enabled: true, transport: "stdio", command: "npx", args: ["-y", "pkg"], env: { TOKEN: "abc" },
    });
    expect(await screen.findByTestId("mcp-card-new")).toBeInTheDocument();
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
  });

  it("add: client-side validation blocks the request; server-side errors stay in the form", async () => {
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Add your first server" }));
    await userEvent.click(screen.getByRole("button", { name: "Add server" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Name is required.");
    expect(m.createMcpServer).not.toHaveBeenCalled();

    m.createMcpServer.mockRejectedValue(new Error(JSON.stringify({ detail: "«bash» مجاز نیست" })));
    await userEvent.type(screen.getByLabelText("Name"), "x");
    await userEvent.type(screen.getByLabelText(/^Command/), "bash");
    await userEvent.click(screen.getByRole("button", { name: "Add server" }));
    expect(await screen.findByText("«bash» مجاز نیست")).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Add MCP server" })).toBeInTheDocument(); // input preserved, not closed
    expect(screen.getByLabelText(/^Command/)).toHaveValue("bash");
  });

  it("switching transport swaps the fields", async () => {
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Add your first server" }));
    await userEvent.selectOptions(screen.getByLabelText("Transport"), "http");
    expect(screen.queryByLabelText(/^Command/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("URL")).toBeInTheDocument();
    expect(screen.getByLabelText(/^Headers/)).toBeInTheDocument();
  });

  it("edit: env values are never shown; removing a saved key sends null; untouched secrets aren't sent", async () => {
    m.listMcpServers.mockResolvedValue([srv({ env_keys: ["OLD", "KEEP"] })]);
    m.updateMcpServer.mockResolvedValue(srv({ env_keys: ["KEEP"] }));
    render(<McpPage />);
    await screen.findByTestId("mcp-card-files");
    await userEvent.click(within(card("files")).getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit MCP server" });
    expect(within(form).getByLabelText(/^Add or replace environment/)).toHaveValue("");
    await userEvent.click(within(form).getByRole("button", { name: "Remove OLD" }));
    expect(within(form).getByRole("button", { name: "Keep OLD" })).toBeInTheDocument();
    await userEvent.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(m.updateMcpServer).toHaveBeenCalledWith(1, {
      name: "files", description: null, transport: "stdio", command: "npx", args: ["-y", "srv"], env: { OLD: null },
    });
  });

  it("edit without touching secrets sends no env key at all", async () => {
    m.listMcpServers.mockResolvedValue([srv({ env_keys: ["KEEP"] })]);
    m.updateMcpServer.mockResolvedValue(srv());
    render(<McpPage />);
    await screen.findByTestId("mcp-card-files");
    await userEvent.click(within(card("files")).getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(m.updateMcpServer.mock.calls[0][1]).not.toHaveProperty("env");
  });

  it("editing a connected server locks connection fields and only sends name/description", async () => {
    m.listMcpServers.mockResolvedValue([srv({ status: "CONNECTED" })]);
    m.updateMcpServer.mockResolvedValue(srv({ status: "CONNECTED", name: "renamed" }));
    render(<McpPage />);
    await screen.findByTestId("mcp-card-files");
    await userEvent.click(within(card("files")).getByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText(/^Command/)).toBeDisabled();
    expect(screen.getByLabelText("Transport")).toBeDisabled();
    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "renamed");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(m.updateMcpServer).toHaveBeenCalledWith(1, { name: "renamed", description: null });
  });

  it("an API failure on an action shows an error and re-syncs the list", async () => {
    m.listMcpServers.mockResolvedValue([srv()]);
    m.connectMcpServer.mockRejectedValue(new Error(JSON.stringify({ detail: "یه عملیات دیگه در حال انجامه." })));
    render(<McpPage />);
    await screen.findByTestId("mcp-card-files");
    const before = m.listMcpServers.mock.calls.length;
    await userEvent.click(within(card("files")).getByRole("button", { name: "Connect" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("یه عملیات دیگه در حال انجامه.");
    await waitFor(() => expect(m.listMcpServers.mock.calls.length).toBeGreaterThan(before));
  });

  it("polling picks up a server that died on its own", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      m.listMcpServers.mockResolvedValueOnce([srv({ status: "CONNECTED" })]).mockResolvedValue([srv({ status: "ERROR", last_error: "connection lost (x)" })]);
      render(<McpPage />);
      await screen.findByText("CONNECTED");
      await vi.advanceTimersByTimeAsync(5100);
      expect(await screen.findByText("connection lost (x)")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
