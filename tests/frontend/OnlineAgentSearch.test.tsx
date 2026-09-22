import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OnlineAgentSearch } from "../src/components/OnlineAgentSearch";

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  return { ...actual, api: { searchAgentRepos: vi.fn(), getAgentInstallCandidates: vi.fn(), installAgent: vi.fn(), getAgentInstall: vi.fn(), listAgentBackends: vi.fn() } };
});
import { api } from "../src/api";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const repo = (over = {}) => ({
  full_name: "NousResearch/hermes-agent", description: "The agent that grows with you", stars: 247718, url: "https://github.com/NousResearch/hermes-agent",
  language: "Python", license: "MIT", last_push: "2026-09-01T00:00:00Z", archived: false, owner_type: "Organization", topics: [], ...over,
});
const cands = (
  commands: { command: string; runnable: boolean; reason?: string | null; kind?: "package" | "script" | null; host?: string | null; notes?: string[] }[]
) => ({
  full_name: "NousResearch/hermes-agent", repo_url: "https://github.com/NousResearch/hermes-agent",
  commands: commands.map((c) => ({ reason: null, kind: c.runnable ? "package" : null, host: null, notes: [], ...c })),
});
const running = (over = {}) => ({ id: "i1", repo: "NousResearch/hermes-agent", command: "pip install hermes-agent", kind: "package", status: "running", log: [], returncode: null, error: null, started_at: 1, finished_at: null, ...over });

beforeEach(() => { Object.values(m).forEach((f) => f.mockReset()); });
afterEach(() => { vi.useRealTimers(); });

async function searchAndOpen() {
  await userEvent.type(screen.getByLabelText("Agent name"), "hermes{Enter}");
  await screen.findByText("NousResearch/hermes-agent");
  await userEvent.click(screen.getByRole("button", { name: "Install options" }));
}

describe("OnlineAgentSearch", () => {
  it("shows trust signals, never a 'verified' label", async () => {
    m.searchAgentRepos.mockResolvedValue([repo(), repo({ full_name: "someone/old-fork", archived: true, owner_type: "User", license: null, stars: 3 })]);
    render(<OnlineAgentSearch />);
    await userEvent.type(screen.getByLabelText("Agent name"), "hermes{Enter}");
    await screen.findByText("NousResearch/hermes-agent");
    expect(screen.getByText("organization")).toBeInTheDocument();
    expect(screen.getByText("archived")).toBeInTheDocument();
    expect(screen.getByText(/no license listed/)).toBeInTheDocument();
    expect(screen.getByText(/247\.7k/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/verified/i);
  });

  it("a runnable command needs an explicit confirm step that shows the exact command", async () => {
    m.searchAgentRepos.mockResolvedValue([repo()]);
    m.getAgentInstallCandidates.mockResolvedValue(cands([{ command: "pip install hermes-agent", runnable: true }]));
    m.installAgent.mockResolvedValue(running());
    render(<OnlineAgentSearch />);
    await searchAndOpen();
    await userEvent.click(await screen.findByRole("button", { name: "Install…" }));

    // nothing was started yet
    expect(m.installAgent).not.toHaveBeenCalled();
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("pip install hermes-agent");
    expect(dialog).toHaveTextContent("NousResearch/hermes-agent");

    await userEvent.click(screen.getByRole("button", { name: /Confirm & install/ }));
    expect(m.installAgent).toHaveBeenCalledWith("NousResearch/hermes-agent", "pip install hermes-agent");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(await screen.findByText("Running…")).toBeInTheDocument();
  });

  it("cancelling the confirmation runs nothing", async () => {
    m.searchAgentRepos.mockResolvedValue([repo()]);
    m.getAgentInstallCandidates.mockResolvedValue(cands([{ command: "pip install hermes-agent", runnable: true }]));
    render(<OnlineAgentSearch />);
    await searchAndOpen();
    await userEvent.click(await screen.findByRole("button", { name: "Install…" }));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(m.installAgent).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("non-runnable commands get a Copy button and the reason, never an Install button", async () => {
    m.searchAgentRepos.mockResolvedValue([repo()]);
    m.getAgentInstallCandidates.mockResolvedValue(
      cands([{ command: "curl -fsSL https://x.sh | sudo bash", runnable: false, reason: "سمت راست pipe فقط bash یا sh می‌تونه باشه (بدون sudo)" }])
    );
    render(<OnlineAgentSearch />);
    await searchAndOpen();
    await screen.findByText(/curl -fsSL/);
    expect(screen.queryByRole("button", { name: "Install…" })).toBeNull();
    expect(screen.getByText(/خودت توی ترمینال اجراش کن/)).toBeInTheDocument();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("curl -fsSL https://x.sh | sudo bash");
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("a runnable script installer (curl|bash) is flagged, names its source host, and warns in the confirm dialog", async () => {
    m.searchAgentRepos.mockResolvedValue([repo()]);
    m.getAgentInstallCandidates.mockResolvedValue(
      cands([
        {
          command: "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash",
          runnable: true,
          kind: "script",
          host: "hermes-agent.nousresearch.com",
        },
      ])
    );
    m.installAgent.mockResolvedValue(running({ command: "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash", kind: "script" }));
    render(<OnlineAgentSearch />);
    await searchAndOpen();
    await screen.findByText(/curl -fsSL/);
    expect(screen.getByText(/downloads & runs a script/)).toBeInTheDocument();
    expect(screen.getByText("از: hermes-agent.nousresearch.com")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Install…" }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("این یه اسکریپت نصبه");
    expect(dialog).toHaveTextContent("hermes-agent.nousresearch.com");
    expect(dialog).toHaveTextContent("curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash");

    await userEvent.click(screen.getByRole("button", { name: /Confirm & install/ }));
    expect(m.installAgent).toHaveBeenCalledWith("NousResearch/hermes-agent", "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash");
  });

  it("a package install confirm dialog does NOT use the script-download wording", async () => {
    m.searchAgentRepos.mockResolvedValue([repo()]);
    m.getAgentInstallCandidates.mockResolvedValue(cands([{ command: "pip install hermes-agent", runnable: true }]));
    render(<OnlineAgentSearch />);
    await searchAndOpen();
    await userEvent.click(await screen.findByRole("button", { name: "Install…" }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).not.toHaveTextContent("این یه اسکریپت نصبه");
    expect(screen.queryByText(/downloads & runs a script/)).toBeNull();
  });

  it("environment notes (e.g. WSL bash) are shown next to the command", async () => {
    m.searchAgentRepos.mockResolvedValue([repo()]);
    m.getAgentInstallCandidates.mockResolvedValue(
      cands([
        {
          command: "curl -fsSL https://x.sh | bash",
          runnable: true,
          kind: "script",
          host: "x.sh",
          notes: ["bash اینجا لانچر WSL هست: اسکریپت داخل WSL اجرا می‌شه، نه روی خود ویندوز"],
        },
      ])
    );
    render(<OnlineAgentSearch />);
    await searchAndOpen();
    expect(await screen.findByText(/لانچر WSL هست/)).toBeInTheDocument();
  });

  it("streams the install log, then re-checks detected agents and notifies the parent on success", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onInstalled = vi.fn();
    m.searchAgentRepos.mockResolvedValue([repo()]);
    m.getAgentInstallCandidates.mockResolvedValue(cands([{ command: "pip install hermes-agent", runnable: true }]));
    m.installAgent.mockResolvedValue(running());
    m.getAgentInstall
      .mockResolvedValueOnce(running({ log: ["Collecting hermes-agent"] }))
      .mockResolvedValue(running({ status: "done", returncode: 0, log: ["Collecting hermes-agent", "Successfully installed hermes-agent"] }));
    const backends = [
      { backend_id: "generic", display_name: "Generic", brand_color: "#777", detected: true, detected_path: null, detected_version: null },
      { backend_id: "hermes", display_name: "Hermes Agent", brand_color: "#f5a623", detected: true, detected_path: "/x/hermes", detected_version: "1.0" },
    ];
    m.listAgentBackends.mockResolvedValue(backends);
    render(<OnlineAgentSearch onInstalled={onInstalled} />);
    await searchAndOpen();
    await userEvent.click(await screen.findByRole("button", { name: "Install…" }));
    await userEvent.click(screen.getByRole("button", { name: /Confirm & install/ }));

    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    await screen.findByText(/Collecting hermes-agent/);
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    expect(await screen.findByText(/Successfully installed hermes-agent/)).toBeInTheDocument();
    expect(await screen.findByText(/Hermes Agent \(detected ✓\)/)).toBeInTheDocument();
    expect(onInstalled).toHaveBeenCalledWith(backends);
    expect(onInstalled).toHaveBeenCalledTimes(1);
  });

  it("failed install shows the error and does NOT claim success", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    m.searchAgentRepos.mockResolvedValue([repo()]);
    m.getAgentInstallCandidates.mockResolvedValue(cands([{ command: "pip install hermes-agent", runnable: true }]));
    m.installAgent.mockResolvedValue(running());
    m.getAgentInstall.mockResolvedValue(running({ status: "error", returncode: 1, error: "نصب با کد خروج 1 تموم شد", log: ["ERROR: no matching distribution"] }));
    render(<OnlineAgentSearch />);
    await searchAndOpen();
    await userEvent.click(await screen.findByRole("button", { name: "Install…" }));
    await userEvent.click(screen.getByRole("button", { name: /Confirm & install/ }));
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    expect(await screen.findByText(/no matching distribution/)).toBeInTheDocument();
    expect(screen.getByText(/نصب با کد خروج 1/)).toBeInTheDocument();
    expect(m.listAgentBackends).not.toHaveBeenCalled();
    expect(screen.queryByText(/نصب تموم شد/)).toBeNull();
  });

  it("server refusal (allow-list) is shown as a readable message", async () => {
    m.searchAgentRepos.mockResolvedValue([repo()]);
    m.getAgentInstallCandidates.mockResolvedValue(cands([{ command: "pip install hermes-agent", runnable: true }]));
    m.installAgent.mockRejectedValue(new Error(JSON.stringify({ detail: "این دستور از داخل برنامه قابل اجرا نیست" })));
    render(<OnlineAgentSearch />);
    await searchAndOpen();
    await userEvent.click(await screen.findByRole("button", { name: "Install…" }));
    await userEvent.click(screen.getByRole("button", { name: /Confirm & install/ }));
    expect(await screen.findByText("این دستور از داخل برنامه قابل اجرا نیست")).toBeInTheDocument();
  });

  it("a README fetch error is readable and retried on re-open; a README with no commands points to the repo", async () => {
    m.searchAgentRepos.mockResolvedValue([repo()]);
    m.getAgentInstallCandidates
      .mockRejectedValueOnce(new Error(JSON.stringify({ detail: "rate limit" })))
      .mockResolvedValueOnce(cands([]));
    render(<OnlineAgentSearch />);
    await searchAndOpen();
    expect(await screen.findByText("rate limit")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Hide" }));
    await userEvent.click(screen.getByRole("button", { name: "Install options" }));
    expect(await screen.findByText(/README رو خودت ببین/)).toHaveAttribute("href", "https://github.com/NousResearch/hermes-agent");
    expect(m.getAgentInstallCandidates).toHaveBeenCalledTimes(2);
    // a successful result is cached: hiding/showing again doesn't refetch
    await userEvent.click(screen.getByRole("button", { name: "Hide" }));
    await userEvent.click(screen.getByRole("button", { name: "Install options" }));
    expect(m.getAgentInstallCandidates).toHaveBeenCalledTimes(2);
  });
});
