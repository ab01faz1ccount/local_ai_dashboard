import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AutoFreeControl } from "../src/components/AutoFreeControl";
import { CustomAgentForm } from "../src/components/CustomAgentForm";
import { QuickConnectPanel } from "../src/components/QuickConnectPanel";
import { OnlineModelSearch } from "../src/components/OnlineModelSearch";

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  const names = [
    "getAutoFree", "setAutoFree", "customAgentPresets", "checkCustomAgent", "updateAgentConfig", "launchPlan", "quickConnect",
    "listModels", "listAgents", "getModelsDir", "listDownloads", "searchHfModels", "listHfFiles",
  ];
  return { ...actual, api: Object.fromEntries(names.map((n) => [n, vi.fn()])) };
});
import { api } from "../src/api";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const off = { mode: "off", grace_minutes: 2, idle_minutes: 10 };
const fit = (label: string, target = "gpu") => ({ label, target, ratio: 0.5, capacity_mb: 8192, estimated_mb: 4096, reason: "because" });

beforeEach(() => {
  Object.values(m).forEach((f) => f.mockReset());
  m.getModelsDir.mockResolvedValue({ path: "/data/models" });
  m.listDownloads.mockResolvedValue([]);
});

describe("AutoFreeControl", () => {
  it("shows the saved policy and saves a new one with its minutes", async () => {
    m.getAutoFree.mockResolvedValue(off);
    m.setAutoFree.mockResolvedValue({ mode: "idle", grace_minutes: 2, idle_minutes: 15 });
    render(<AutoFreeControl runtimeId={4} />);
    expect(await screen.findByText(/Stays loaded until you stop it/)).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Free memory"), "idle");
    const minutes = screen.getByLabelText("Minutes");
    await userEvent.clear(minutes);
    await userEvent.type(minutes, "15");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(m.setAutoFree).toHaveBeenCalledWith(4, { mode: "idle", idle_minutes: 15 });
    expect(await screen.findByText(/after 15 min with no activity/)).toBeInTheDocument();
  });

  it("blocks saving invalid minutes and says why", async () => {
    m.getAutoFree.mockResolvedValue(off);
    render(<AutoFreeControl runtimeId={4} />);
    await screen.findByText(/Stays loaded/);
    await userEvent.selectOptions(screen.getByLabelText("Free memory"), "after_agent");
    const minutes = screen.getByLabelText("Minutes");
    await userEvent.clear(minutes);
    await userEvent.type(minutes, "0");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByText(/whole number of minutes/)).toBeInTheDocument();
  });

  it("Save stays disabled until something changed", async () => {
    m.getAutoFree.mockResolvedValue(off);
    render(<AutoFreeControl runtimeId={4} />);
    await screen.findByText(/Stays loaded/);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

describe("CustomAgentForm", () => {
  const agent = { id: 9, name: "My agent", description: null, agent_type: "generic", agent_backend: "custom_cli", default_runtime_id: null, config_json: {}, status: "INACTIVE" } as never;
  const preset = { id: "openai_env", label: "Reads the standard OpenAI environment variables", config: { mode: "env_only" }, env: { OPENAI_BASE_URL: "{base_url}" } };

  it("a template fills the spec with the agent's name", async () => {
    m.customAgentPresets.mockResolvedValue([preset]);
    render(<CustomAgentForm agent={agent} onSaved={() => {}} />);
    await screen.findByRole("option", { name: preset.label });
    await userEvent.selectOptions(screen.getByLabelText("Start from"), "openai_env");
    const box = screen.getByLabelText(/How to run and configure it/) as HTMLTextAreaElement;
    expect(JSON.parse(box.value)).toMatchObject({ name: "My agent", config: { mode: "env_only" } });
  });

  it("Check shows what would be written, and Save stores the validated spec", async () => {
    m.customAgentPresets.mockResolvedValue([]);
    const spec = { name: "My agent", executable: "mycli", config: { mode: "env_only" }, env: { OPENAI_BASE_URL: "{base_url}" } };
    m.checkCustomAgent.mockResolvedValue({
      ok: true, spec, detected: false, detected_path: null, detected_version: null,
      preview: { settings: {}, env: { OPENAI_BASE_URL: "http://127.0.0.1:8080/v1" } },
    });
    m.updateAgentConfig.mockResolvedValue({ ...(agent as object), config_json: { cli_spec: spec } });
    const onSaved = vi.fn();
    render(<CustomAgentForm agent={agent} onSaved={onSaved} />);
    const box = screen.getByLabelText(/How to run and configure it/);
    await userEvent.click(box);
    await userEvent.paste(JSON.stringify(spec));

    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText(/Not found on this computer/)).toBeInTheDocument();
    expect(screen.getByText("OPENAI_BASE_URL=http://127.0.0.1:8080/v1")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(m.updateAgentConfig).toHaveBeenCalledWith(9, { agent_backend: "custom_cli", config_json: { cli_spec: spec } });
  });

  it("invalid JSON never reaches the server", async () => {
    m.customAgentPresets.mockResolvedValue([]);
    render(<CustomAgentForm agent={agent} onSaved={() => {}} />);
    await userEvent.click(screen.getByLabelText(/How to run and configure it/));
    await userEvent.paste("{nope");
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText(/isn't valid JSON/)).toBeInTheDocument();
    expect(m.checkCustomAgent).not.toHaveBeenCalled();
  });

  it("a spec the server rejects shows the server's reason", async () => {
    m.customAgentPresets.mockResolvedValue([]);
    m.checkCustomAgent.mockRejectedValue(new Error(JSON.stringify({ detail: "executable is required" })));
    render(<CustomAgentForm agent={agent} onSaved={() => {}} />);
    await userEvent.click(screen.getByLabelText(/How to run and configure it/));
    await userEvent.paste('{"name":"x"}');
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText("executable is required")).toBeInTheDocument();
  });
});

describe("QuickConnectPanel", () => {
  const model = { id: 3, name: "Cool-3B" };
  const agentRow = { id: 7, name: "Hermes", agent_backend: "hermes" };

  beforeEach(() => {
    m.listModels.mockResolvedValue([model]);
    m.listAgents.mockResolvedValue([agentRow, { id: 8, name: "Plain", agent_backend: "generic" }]);
  });

  it("only offers agents that have a real backend", async () => {
    render(<QuickConnectPanel />);
    expect(await screen.findByRole("option", { name: "Hermes" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Plain" })).toBeNull();
  });

  it("Preview shows the fit and the chosen settings without connecting anything", async () => {
    m.launchPlan.mockResolvedValue({
      runtime: { name: "Cool-3B", executable_path: "x", host: "127.0.0.1", port: 8080, config_json: { ctx_size: 16384, n_gpu_layers: 999 } },
      fit: fit("GOOD"), notes: ["The model fits in VRAM, so every layer is offloaded to the GPU."],
    });
    render(<QuickConnectPanel />);
    await userEvent.selectOptions(await screen.findByLabelText("Model"), "3");
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText("ctx_size = 16384")).toBeInTheDocument();
    expect(screen.getByText(/every layer is offloaded/)).toBeInTheDocument();
    expect(m.quickConnect).not.toHaveBeenCalled();
  });

  it("Use this model connects the chosen model and agent, reports the outcome and tells the caller", async () => {
    m.quickConnect.mockResolvedValue({
      created_runtime: true, plan: null, started: true, error: null, runtime: { status: "ONLINE" },
      agent: { name: "Hermes" }, verification: { ok: true, verified: true, checks: [] },
      endpoint: { base_url: "http://127.0.0.1:8080/v1", model: "Cool-3B", context_length: null, has_key: false },
    });
    const onConnected = vi.fn();
    render(<QuickConnectPanel onConnected={onConnected} />);
    await userEvent.selectOptions(await screen.findByLabelText("Model"), "3");
    await userEvent.selectOptions(screen.getByLabelText("Agent (optional)"), "7");
    await userEvent.click(screen.getByRole("button", { name: "Use this model" }));
    expect(m.quickConnect).toHaveBeenCalledWith({ model_id: 3, agent_id: 7 });
    expect(await screen.findByText(/configured Hermes to use it/)).toBeInTheDocument();
    expect(onConnected).toHaveBeenCalled();
  });

  it("a runtime that did not start is shown as an error, with the reason", async () => {
    m.quickConnect.mockResolvedValue({
      created_runtime: true, plan: null, started: false, error: "port busy", runtime: { status: "ERROR" },
      endpoint: { base_url: "x", model: null, context_length: null, has_key: false },
    });
    render(<QuickConnectPanel />);
    await userEvent.selectOptions(await screen.findByLabelText("Model"), "3");
    await userEvent.click(screen.getByRole("button", { name: "Use this model" }));
    expect(await screen.findByText(/did not start: port busy/)).toBeInTheDocument();
  });

  it("buttons stay disabled until a model is chosen", async () => {
    render(<QuickConnectPanel />);
    await screen.findByRole("option", { name: "Cool-3B" });
    expect(screen.getByRole("button", { name: "Use this model" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Preview" })).toBeDisabled();
  });
});

describe("OnlineModelSearch fit", () => {
  it("shows each file's fit, marks the recommended one and lists it first", async () => {
    m.searchHfModels.mockResolvedValue({
      query: { text: "cool", size_b: null, quant: null, purpose: "any", corrected_from: null },
      results: [
        { repo_id: "acme/Cool-3B-GGUF", author: "acme", name: "Cool-3B-GGUF", downloads: 1, likes: 1, updated: "2026-02-01T00:00:00Z", gated: false, url: "https://huggingface.co/acme/Cool-3B-GGUF", relevance: 1 },
      ],
    });
    const f = (name: string, size: number, label: string, recommended: boolean) => ({
      filename: name, display_name: name.replace(".gguf", ""), quantization: null, size_bytes: size, parts: 1, verifiable: false, files: [],
      hardware_fit: fit(label), gpu_fit: label, recommended,
    });
    m.listHfFiles.mockResolvedValue([f("Big.gguf", 9_000_000_000, "NOT_RECOMMENDED", false), f("Good.gguf", 2_000_000_000, "GOOD", true)]);
    render(<OnlineModelSearch />);
    await userEvent.type(screen.getByLabelText("Model name"), "cool{Enter}");
    await userEvent.click(await screen.findByRole("button", { name: "Show files" }));
    expect(await screen.findByText("Recommended for this PC")).toBeInTheDocument();
    const rows = document.querySelectorAll(".browse-file-row");
    expect(rows[0].textContent).toContain("Good");
    expect(rows[1].textContent).toContain("Big");
    expect(document.querySelectorAll(".fit-badge").length).toBe(2);
  });
});
