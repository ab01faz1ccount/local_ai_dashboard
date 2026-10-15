import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowseTab } from "../src/components/BrowseTab";
import { RuntimeCard } from "../src/components/RuntimeCard";
import { OnboardingWizard } from "../src/OnboardingWizard";
import { SettingsPage } from "../src/SettingsPage";
import { SetupPage } from "../src/SetupPage";
import { listing } from "./helpers";

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  const names = [
    "fsList","registerLocalModel","registerLocalAgent","getConnectivity","getModelsDir","listDownloads","searchHfModels","listHfFiles",
    "startModelDownload","cancelDownload","setModelsDir","searchAgentRepos","getAgentInstallCandidates","installAgent","getAgentInstall",
    "listAgentBackends","getOnboardingState","listRuntimes","listAgents","listModels","patchOnboardingState","verifyExecutablePath",
    "detectLlamaCpp","getInstallGuidance","scanModels","createRuntime","startRuntime","createAgent","configureAgent","attachAgent",
    "updateRuntimeConfig","updateAgentConfig","deleteRuntime","deleteAgent",
    "getAutoFree","setAutoFree","customAgentPresets","checkCustomAgent","launchPlan","quickConnect",
  ];
  return { ...actual, api: Object.fromEntries(names.map((n) => [n, vi.fn()])) };
});
import { api } from "../src/api";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const model = (id: number, name: string) => ({
  id, name, file_path: `/m/${name}.gguf`, file_size_bytes: 3_000_000_000, quantization: null, param_count: null, context_length: null,
  hf_repo_id: null, hf_filename: null, verification_status: null, verification_checked_at: null, verification_details: {},
});

beforeEach(() => {
  Object.values(m).forEach((f) => f.mockReset());
  m.getConnectivity.mockResolvedValue({ online: true });
  m.getModelsDir.mockResolvedValue({ path: "/data/models" });
  m.listDownloads.mockResolvedValue([]);
  m.getAutoFree.mockResolvedValue({ mode: "off", grace_minutes: 2, idle_minutes: 10 });
  m.customAgentPresets.mockResolvedValue([]);
  m.fsList.mockImplementation(async ({ path }: { path?: string | null }) =>
    listing(path ?? "/home/u", [{ name: "Tiny.gguf", size: 5000 }, { name: "hermes" }, { name: "llama-server" }])
  );
});

describe("BrowseTab", () => {
  it("offline + models: picking a .gguf file registers exactly that path and lists the result", async () => {
    m.registerLocalModel.mockResolvedValue([model(1, "Tiny")]);
    const onChanged = vi.fn();
    render(<BrowseTab onChanged={onChanged} />);
    await userEvent.click(screen.getByRole("button", { name: "Choose a .gguf file…" }));
    expect(m.fsList).toHaveBeenLastCalledWith(expect.objectContaining({ kind: "file", extensions: [".gguf"] }));
    await userEvent.dblClick(await screen.findByText("Tiny.gguf"));
    expect(m.registerLocalModel).toHaveBeenCalledWith("/home/u/Tiny.gguf");
    expect(await screen.findByText(/1 model\(s\) in your list/)).toBeInTheDocument();
    expect(screen.getByText("Tiny")).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("offline + models: picking a folder registers the folder; backend errors are readable", async () => {
    m.registerLocalModel.mockRejectedValue(new Error(JSON.stringify({ detail: "فقط فایل .gguf قابل اضافه شدنه" })));
    render(<BrowseTab />);
    await userEvent.click(screen.getByRole("button", { name: "Choose a folder…" }));
    expect(m.fsList).toHaveBeenLastCalledWith(expect.objectContaining({ kind: "dir" }));
    await userEvent.click(await screen.findByRole("button", { name: "Select this folder" }));
    expect(m.registerLocalModel).toHaveBeenCalledWith("/home/u");
    expect(await screen.findByText("فقط فایل .gguf قابل اضافه شدنه")).toBeInTheDocument();
  });

  it("offline + agents: only launchable files are offered, and success shows path and version", async () => {
    m.registerLocalAgent.mockResolvedValue({
      agent: { id: 3, name: "Hermes Agent", description: null, agent_type: "generic", agent_backend: "hermes", default_runtime_id: null, config_json: {}, status: "INACTIVE" },
      created: true, path: "/opt/hermes/hermes", detected_version: "hermes 1.2.3",
    });
    const onChanged = vi.fn();
    render(<BrowseTab onChanged={onChanged} />);
    await userEvent.click(screen.getByRole("button", { name: "Agents (CLI)" }));
    await userEvent.click(screen.getByRole("button", { name: /Choose the agent's executable/ }));
    expect(m.fsList).toHaveBeenLastCalledWith(expect.objectContaining({ executableOnly: true }));
    await userEvent.dblClick(await screen.findByText("hermes"));
    expect(m.registerLocalAgent).toHaveBeenCalledWith("/home/u/hermes");
    expect(await screen.findByText(/Hermes Agent added/)).toBeInTheDocument();
    expect(screen.getByText(/hermes 1.2.3/)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it("offline agent rejection message is shown", async () => {
    m.registerLocalAgent.mockRejectedValue(new Error(JSON.stringify({ detail: "اسم این فایل با هیچ backend شناخته‌شده‌ای نمی‌خونه" })));
    render(<BrowseTab />);
    await userEvent.click(screen.getByRole("button", { name: "Agents (CLI)" }));
    await userEvent.click(screen.getByRole("button", { name: /Choose the agent's executable/ }));
    await userEvent.dblClick(await screen.findByText("llama-server"));
    expect(await screen.findByText(/هیچ backend شناخته‌شده‌ای/)).toBeInTheDocument();
  });

  it("online mode checks connectivity and disables searching with a banner when offline", async () => {
    m.getConnectivity.mockResolvedValue({ online: false });
    render(<BrowseTab />);
    await userEvent.click(screen.getByRole("button", { name: /Online/ }));
    expect(await screen.findByText(/نیاز به اتصال اینترنت دارد/)).toBeInTheDocument();
    expect(m.getConnectivity).toHaveBeenCalledWith(true);
    expect(screen.getByLabelText("Model name")).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Agents (CLI)" }));
    expect(screen.getByLabelText("Agent name")).toBeDisabled();
  });

  it("online mode when connected enables search for both kinds", async () => {
    render(<BrowseTab />);
    await userEvent.click(screen.getByRole("button", { name: /Online/ }));
    await waitFor(() => expect(screen.getByLabelText("Model name")).toBeEnabled());
    expect(screen.queryByText(/نیاز به اتصال اینترنت دارد/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Agents (CLI)" }));
    expect(screen.getByLabelText("Agent name")).toBeEnabled();
  });
});

describe("RuntimeCard edit -> Browse for the executable", () => {
  const runtime = (status: "OFFLINE" | "ONLINE") => ({
    id: 1, name: "LLM1", engine_type: "llama.cpp", model_id: null, model_name: null, host: "127.0.0.1", port: 8080,
    executable_path: "/old/llama-server", status, pid: null, last_error: null, config_json: {}, source_system: "local",
  });
  const props = { metrics: undefined, models: [], onStart: vi.fn(), onStop: vi.fn(), onRestart: vi.fn(), onDelete: vi.fn() };

  it("fills the executable field from the picker and saves it through onEdit", async () => {
    const onEdit = vi.fn().mockResolvedValue(undefined);
    render(<RuntimeCard runtime={runtime("OFFLINE")} onEdit={onEdit} {...props} />);
    // like the real backend: a file path as the start point shows that file's folder
    m.fsList.mockResolvedValue(listing("/opt/llama", [{ name: "llama-server" }]));
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Browse…" }));
    expect(m.fsList).toHaveBeenLastCalledWith(expect.objectContaining({ path: "/old/llama-server", executableOnly: true }));
    await userEvent.dblClick(await screen.findByText("llama-server"));
    expect(screen.getByDisplayValue("/opt/llama/llama-server")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onEdit).toHaveBeenCalledWith(1, { executable_path: "/opt/llama/llama-server" });
  });

  it("Browse is disabled while the runtime is running (same rule as the field itself)", async () => {
    render(<RuntimeCard runtime={runtime("ONLINE")} onEdit={vi.fn()} {...props} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByRole("button", { name: "Browse…" })).toBeDisabled();
  });
});

describe("Settings page", () => {
  it("no longer has a Browse tab (finding models/agents lives in Setup)", async () => {
    m.listRuntimes.mockResolvedValue([]);
    m.listAgents.mockResolvedValue([]);
    m.listAgentBackends.mockResolvedValue([]);
    render(<SettingsPage />);
    await waitFor(() => expect(m.listAgents).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Browse" })).toBeNull();
    expect(screen.getByRole("tab", { name: "Agents" })).toBeInTheDocument();
  });
});

describe("Setup page", () => {
  beforeEach(() => {
    m.getOnboardingState.mockResolvedValue({ llama_cpp_detected: true, llama_cpp_path: "/usr/bin/llama-server", first_model_added: false, first_runtime_configured: false, first_agent_created: false, wizard_completed: false });
    m.listRuntimes.mockResolvedValue([]);
    m.listModels.mockResolvedValue([]);
  });

  it("shows the options first, and the LLM/Agent filter narrows them", async () => {
    render(<SetupPage onDone={() => {}} />);
    expect(screen.getByRole("button", { name: /Run an LLM/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add a model/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add an agent/ })).toBeInTheDocument();
    expect(screen.queryByText("Find llama.cpp", { selector: "h2" })).toBeNull(); // no step content yet

    await userEvent.click(screen.getByRole("button", { name: "Agent" }));
    expect(screen.getByRole("button", { name: /Add an agent/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Run an LLM/ })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "LLM" }));
    expect(screen.queryByRole("button", { name: /Add an agent/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Add a model/ })).toBeInTheDocument();
  });

  it("Add an agent opens the agent finder (no Models/Agents switch) and can go back", async () => {
    m.registerLocalAgent.mockResolvedValue({
      agent: { id: 3, name: "Hermes Agent", description: null, agent_type: "generic", agent_backend: "hermes", default_runtime_id: null, config_json: {}, status: "INACTIVE" },
      created: true, path: "/opt/hermes", detected_version: null,
    });
    render(<SetupPage onDone={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /Add an agent/ }));
    expect(screen.queryByRole("button", { name: "Models (LLM)" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Choose the agent's executable/ }));
    await userEvent.dblClick(await screen.findByText("hermes"));
    expect(m.registerLocalAgent).toHaveBeenCalledWith("/home/u/hermes");

    await userEvent.click(screen.getByRole("button", { name: "← All setup options" }));
    expect(screen.getByRole("button", { name: /Run an LLM/ })).toBeInTheDocument();
  });

  it("Add a model opens the model finder", async () => {
    render(<SetupPage onDone={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /Add a model/ }));
    expect(screen.getByRole("button", { name: "Choose a .gguf file…" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Agents (CLI)" })).toBeNull();
  });

  it("Run an LLM opens the wizard; its back link returns to the options", async () => {
    render(<SetupPage onDone={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /Run an LLM/ }));
    expect(await screen.findByText("Find or install llama.cpp")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "← All setup options" }));
    expect(screen.getByRole("button", { name: /Add an agent/ })).toBeInTheDocument();
  });
});

describe("Setup wizard", () => {
  const baseState = { llama_cpp_detected: true, llama_cpp_path: "/usr/bin/llama-server", first_model_added: false, first_runtime_configured: false, first_agent_created: false, wizard_completed: false };
  beforeEach(() => {
    m.getOnboardingState.mockResolvedValue(baseState);
    m.listRuntimes.mockResolvedValue([]);
    m.listAgents.mockResolvedValue([]);
    m.listModels.mockResolvedValue([]);
    m.listAgentBackends.mockResolvedValue([]);
  });

  it("step 1 no longer shows the hardcoded starter list; it offers online search instead", async () => {
    render(<OnboardingWizard onComplete={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Continue" }));
    expect(await screen.findByText("Pick a model")).toBeInTheDocument();
    expect(screen.getByLabelText("Model name")).toBeInTheDocument();
    expect(screen.queryByText(/Open on Hugging Face/)).toBeNull();
    expect(screen.queryByText(/Llama 3.2 3B|Qwen2.5 7B|Mistral 7B/)).toBeNull();
  });

  it("a finished online download gets selected so Continue works", async () => {
    m.listDownloads.mockResolvedValueOnce([]).mockResolvedValue([]);
    m.listModels.mockResolvedValueOnce([]).mockResolvedValue([model(9, "Cool")]);
    m.searchHfModels.mockResolvedValue({ query: { text: "cool", size_b: null, quant: null, purpose: "any", corrected_from: null }, results: [{ repo_id: "a/Cool", author: "a", name: "Cool", downloads: 1, likes: 1, updated: null, gated: false, url: "https://huggingface.co/a/Cool", relevance: 1 }] });
    m.listHfFiles.mockResolvedValue([{ filename: "Cool-Q4_K_M.gguf", display_name: "Cool-Q4_K_M", quantization: "Q4_K_M", size_bytes: 10, parts: 1, verifiable: true, files: [] }]);
    m.startModelDownload.mockResolvedValue({ id: "j", repo_id: "a/Cool", filename: "Cool-Q4_K_M.gguf", status: "downloading", downloaded_bytes: 1, total_bytes: 10, current_file: null, note: null, speed_bps: 0, error: null, local_path: null, result: {}, started_at: 1, finished_at: null });
    render(<OnboardingWizard onComplete={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Continue" }));
    expect(screen.getAllByRole("button", { name: "Continue" }).at(-1)).toBeDisabled();
    // simulate: polling finds it done
    m.listDownloads.mockResolvedValue([{ id: "j", repo_id: "a/Cool", filename: "Cool-Q4_K_M.gguf", status: "done", downloaded_bytes: 10, total_bytes: 10, current_file: null, note: null, speed_bps: 0, error: null, local_path: "/x", result: { registered: true, model_id: 9, model_name: "Cool" }, started_at: 1, finished_at: 2 }]);
    await userEvent.type(screen.getByLabelText("Model name"), "cool{Enter}");
    await userEvent.click(await screen.findByRole("button", { name: "Show files" }));
    await userEvent.click(await screen.findByRole("button", { name: "Download" }));
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("9"), { timeout: 4000 });
    expect(screen.getAllByRole("button", { name: "Continue" }).at(-1)).toBeEnabled();
  });

  it("step 0 Browse… picks the executable and verifies it immediately", async () => {
    m.getOnboardingState.mockResolvedValue({ ...baseState, llama_cpp_detected: false, llama_cpp_path: null });
    m.verifyExecutablePath.mockResolvedValue({ valid: true, path: "/home/u/llama-server" });
    render(<OnboardingWizard onComplete={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Browse…" }));
    await userEvent.dblClick(await screen.findByText("llama-server"));
    expect(m.verifyExecutablePath).toHaveBeenCalledWith("/home/u/llama-server");
    expect(await screen.findByText(/Verified: \/home\/u\/llama-server/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  it("step 1: picking a single .gguf file registers it and selects it", async () => {
    m.registerLocalModel.mockResolvedValue([model(5, "Tiny")]);
    m.listModels.mockResolvedValueOnce([]).mockResolvedValue([model(5, "Tiny")]);
    render(<OnboardingWizard onComplete={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Continue" }));
    await userEvent.click(await screen.findByRole("button", { name: "Or pick a single .gguf file…" }));
    await userEvent.dblClick(await screen.findByText("Tiny.gguf"));
    expect(m.registerLocalModel).toHaveBeenCalledWith("/home/u/Tiny.gguf");
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("5"));
    expect(screen.getAllByRole("button", { name: "Continue" }).at(-1)).toBeEnabled();
  });

  it("finishing the LLM flow ends on a done screen and never asks about agents", async () => {
    m.listModels.mockResolvedValue([model(5, "Tiny")]);
    m.createRuntime.mockResolvedValue({ id: 7, status: "STOPPED" });
    m.startRuntime.mockResolvedValue({});
    m.listRuntimes.mockResolvedValueOnce([]).mockResolvedValue([{ id: 7, status: "ONLINE", last_error: null }]);
    m.patchOnboardingState.mockResolvedValue({});
    const done = vi.fn();
    render(<OnboardingWizard onComplete={done} />);
    await userEvent.click(await screen.findByRole("button", { name: "Continue" }));
    await userEvent.selectOptions(await screen.findByRole("combobox"), "5");
    await userEvent.click(screen.getAllByRole("button", { name: "Continue" }).at(-1)!);
    await userEvent.click(await screen.findByRole("button", { name: "Create & start" }));
    await userEvent.click(await screen.findByRole("button", { name: "Finish" }));
    expect(await screen.findByTestId("llm-done")).toBeInTheDocument();
    expect(m.patchOnboardingState).toHaveBeenCalledWith({ wizard_completed: true });
    expect(screen.queryByText(/Attach an agent/)).toBeNull();
    expect(m.createAgent).not.toHaveBeenCalled();
    expect(m.attachAgent).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Go to dashboard" }));
    expect(done).toHaveBeenCalled();
  });
});
