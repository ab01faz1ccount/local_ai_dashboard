import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OnlineModelSearch } from "../src/components/OnlineModelSearch";
import type { DownloadJob } from "../src/api";

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  return {
    ...actual,
    api: {
      getModelsDir: vi.fn(), listDownloads: vi.fn(), searchHfModels: vi.fn(), listHfFiles: vi.fn(),
      startModelDownload: vi.fn(), cancelDownload: vi.fn(), setModelsDir: vi.fn(), fsList: vi.fn(),
    },
  };
});
import { api } from "../src/api";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const resp = (results: unknown[], query: Record<string, unknown> = {}) => ({ query: { text: "q", size_b: null, quant: null, purpose: "any", corrected_from: null, ...query }, results });
const result = (repo: string, extra = {}) => ({
  repo_id: repo, author: repo.split("/")[0], name: repo.split("/")[1], downloads: 12345, likes: 7,
  updated: "2026-02-01T00:00:00Z", gated: false, url: `https://huggingface.co/${repo}`, relevance: 1, ...extra,
});
const file = (name: string, quant: string, size: number, extra = {}) => ({
  filename: name, display_name: name.replace(".gguf", ""), quantization: quant, size_bytes: size, parts: 1, verifiable: true,
  files: [{ path: name, size, sha256: "a".repeat(64) }], ...extra,
});
const job = (over: Partial<DownloadJob> = {}): DownloadJob => ({
  id: "j1", repo_id: "acme/Cool-3B-GGUF", filename: "Cool-Q4_K_M.gguf", status: "downloading", downloaded_bytes: 500, total_bytes: 1000,
  current_file: "Cool-Q4_K_M.gguf", note: null, speed_bps: 2048, error: null, local_path: null, result: {}, started_at: 1, finished_at: null, ...over,
});

beforeEach(() => {
  Object.values(m).forEach((f) => f.mockReset());
  m.getModelsDir.mockResolvedValue({ path: "/data/models" });
  m.listDownloads.mockResolvedValue([]);
});
afterEach(() => { vi.useRealTimers(); });

describe("OnlineModelSearch", () => {
  it("searches, shows files for a repo, and starts a download for the chosen quantization", async () => {
    m.searchHfModels.mockResolvedValue(resp([result("acme/Cool-3B-GGUF"), result("zed/Cool-3B-Instruct", { gated: true })]));
    m.listHfFiles.mockResolvedValue([file("Cool-Q4_K_M.gguf", "Q4_K_M", 2_000_000_000), file("Cool-Q8_0.gguf", "Q8_0", 3_400_000_000)]);
    m.startModelDownload.mockResolvedValue(job());
    render(<OnlineModelSearch />);

    expect(await screen.findByText("/data/models")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Model name"), "cool 3b{Enter}");
    expect(m.searchHfModels).toHaveBeenCalledWith("cool 3b");
    await screen.findByText("acme/Cool-3B-GGUF");

    await userEvent.click(screen.getAllByRole("button", { name: "Show files" })[0]);
    await screen.findByText("Cool-Q4_K_M");
    expect(screen.getByText("Q4_K_M")).toBeInTheDocument();
    expect(screen.getByText("1.9 GB")).toBeInTheDocument();
    expect(screen.getAllByText("SHA256 check").length).toBe(2);

    await userEvent.click(screen.getAllByRole("button", { name: "Download" })[0]);
    expect(m.startModelDownload).toHaveBeenCalledWith("acme/Cool-3B-GGUF", "Cool-Q4_K_M.gguf");
    // the started job shows up with progress and disables re-downloading that file
    expect(await screen.findByText(/500 B \/ 1000 B \(50%\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Downloading…" })).toBeDisabled();
    expect(screen.getAllByRole("button", { name: "Download" })).toHaveLength(1); // the other quant is still available
  });

  it("gated repos are flagged and cannot be downloaded from here", async () => {
    m.searchHfModels.mockResolvedValue(resp([result("zed/Gated-7B", { gated: true })]));
    m.listHfFiles.mockResolvedValue([file("g.gguf", "Q4_K_M", 100)]);
    render(<OnlineModelSearch />);
    await userEvent.type(screen.getByLabelText("Model name"), "gated{Enter}");
    await screen.findByText("gated");
    await userEvent.click(screen.getByRole("button", { name: "Show files" }));
    await screen.findByText("g");
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
  });

  it("shows friendly errors from the backend (offline / rate limit) instead of raw JSON", async () => {
    m.searchHfModels.mockRejectedValue(new Error(JSON.stringify({ detail: "این بخش نیاز به اتصال اینترنت دارد." })));
    render(<OnlineModelSearch />);
    await userEvent.type(screen.getByLabelText("Model name"), "x{Enter}");
    expect(await screen.findByText("این بخش نیاز به اتصال اینترنت دارد.")).toBeInTheDocument();
  });

  it("empty result and empty file list have their own messages", async () => {
    m.searchHfModels.mockResolvedValueOnce(resp([]));
    render(<OnlineModelSearch />);
    await userEvent.type(screen.getByLabelText("Model name"), "zzz{Enter}");
    expect(await screen.findByText(/چیزی پیدا نشد/)).toBeInTheDocument();
    m.searchHfModels.mockResolvedValueOnce(resp([result("a/b")]));
    m.listHfFiles.mockResolvedValueOnce(resp([]));
    await userEvent.clear(screen.getByLabelText("Model name"));
    await userEvent.type(screen.getByLabelText("Model name"), "b{Enter}");
    await userEvent.click(await screen.findByRole("button", { name: "Show files" }));
    expect(await screen.findByText(/فایل GGUF قابل اجرایی/)).toBeInTheDocument();
  });

  it("polls progress, then reports the finished+registered model exactly once", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onModelAdded = vi.fn();
    m.listDownloads
      .mockResolvedValueOnce([job()])                                   // on mount: one already running
      .mockResolvedValueOnce([job({ downloaded_bytes: 900 })])          // poll 1
      .mockResolvedValue([job({ status: "done", downloaded_bytes: 1000, result: { registered: true, model_id: 42, model_name: "Cool" } })]);
    render(<OnlineModelSearch onModelAdded={onModelAdded} />);
    await screen.findByText(/500 B \/ 1000 B/);

    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    await screen.findByText(/900 B \/ 1000 B/);
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    await screen.findByText(/Added to your models: Cool/);
    expect(onModelAdded).toHaveBeenCalledTimes(1);
    expect(onModelAdded).toHaveBeenCalledWith(42);

    // polling stops once nothing is active, so it can't fire again
    const calls = m.listDownloads.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(m.listDownloads.mock.calls.length).toBe(calls);
    expect(onModelAdded).toHaveBeenCalledTimes(1);
  });

  it("a job that finished before mount doesn't re-fire onModelAdded", async () => {
    const onModelAdded = vi.fn();
    m.listDownloads.mockResolvedValue([job({ status: "done", result: { registered: true, model_id: 1 } })]);
    render(<OnlineModelSearch onModelAdded={onModelAdded} />);
    await screen.findByText(/Added to your models/);
    expect(onModelAdded).not.toHaveBeenCalled();
  });

  it("failed download shows the reason; registration failure points at the file on disk", async () => {
    m.listDownloads.mockResolvedValue([
      job({ id: "e", status: "error", error: "هش SHA256 فایل با هش رسمی نمی‌خونه" }),
      job({ id: "r", status: "done", local_path: "/data/models/a/b/x.gguf", result: { registered: false, register_error: "db locked" } }),
    ]);
    render(<OnlineModelSearch />);
    expect(await screen.findByText(/SHA256 فایل/)).toBeInTheDocument();
    expect(screen.getByText(/couldn't be added to the catalog \(db locked\)/)).toBeInTheDocument();
    expect(screen.getByText("/data/models/a/b/x.gguf")).toBeInTheDocument();
  });

  it("cancel calls the API; disabled prop blocks searching", async () => {
    m.listDownloads.mockResolvedValue([job()]);
    m.cancelDownload.mockResolvedValue({ cancelling: true });
    const { rerender } = render(<OnlineModelSearch />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(m.cancelDownload).toHaveBeenCalledWith("j1");
    rerender(<OnlineModelSearch disabled />);
    expect(screen.getByLabelText("Model name")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
  });

  it("changing the download folder goes through the folder picker and the API", async () => {
    m.fsList.mockResolvedValue({ path: "/mnt/big", parent: "/mnt", entries: [], roots: [], truncated: false });
    m.setModelsDir.mockResolvedValue({ path: "/mnt/big" });
    render(<OnlineModelSearch />);
    await userEvent.click(await screen.findByRole("button", { name: "Choose folder…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Select this folder" }));
    expect(m.setModelsDir).toHaveBeenCalledWith("/mnt/big");
    expect(await screen.findByText("/mnt/big")).toBeInTheDocument();
  });
});

describe("OnlineModelSearch ranking UI", () => {
  const trusted = { tier: "quantizer", label: "Well-known GGUF publisher", followers: 4200 };
  const unknown = { tier: "unknown", label: "Publisher not on the known list", followers: null };

  async function searchFor(text: string) {
    render(<OnlineModelSearch />);
    await userEvent.type(screen.getByLabelText("Model name"), `${text}{Enter}`);
  }

  it("shows trust signals, keeps modified versions apart and marks the best match", async () => {
    m.searchHfModels.mockResolvedValue(resp([
      result("lmstudio-community/Qwen3.5-9B-GGUF", { role: "conversion", trust: trusted, why: ["Same name as your search", "4.2k followers"] }),
      result("nobody/Qwen3.5-9B-Uncensored", { role: "variant", trust: unknown, extra_words: ["uncensored"] }),
    ]));
    await searchFor("qwen3.5 9b");
    expect(await screen.findByText("lmstudio-community/Qwen3.5-9B-GGUF")).toBeInTheDocument();
    expect(screen.getByText("Best match")).toBeInTheDocument();
    expect(screen.getByText("Known publisher")).toBeInTheDocument();
    expect(screen.getByText("4.2k followers")).toBeInTheDocument();
    expect(screen.getByText("followers n/a")).toBeInTheDocument();
    expect(screen.getByText(/Same name as your search/)).toBeInTheDocument();
    const details = document.querySelector("details.variants") as HTMLDetailsElement;
    expect(details).not.toBeNull();
    expect(details.open).toBe(false);
    expect(details.textContent).toMatch(/1 modified version/);
    expect(details.textContent).toContain("nobody/Qwen3.5-9B-Uncensored");
  });

  it("'Trusted publishers only' hides unknown publishers and says how many are hidden", async () => {
    m.searchHfModels.mockResolvedValue(resp([
      result("lmstudio-community/A-9B", { role: "conversion", trust: trusted }),
      result("nobody/A-9B", { role: "conversion", trust: unknown }),
    ]));
    await searchFor("a 9b");
    await screen.findByText("nobody/A-9B");
    await userEvent.click(screen.getByRole("button", { name: "Trusted publishers only" }));
    expect(screen.queryByText("nobody/A-9B")).toBeNull();
    expect(screen.getByText("lmstudio-community/A-9B")).toBeInTheDocument();
    expect(screen.getByText(/1 hidden by filters/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByText("nobody/A-9B")).toBeInTheDocument();
  });

  it("says when the filters hide everything instead of looking like 'no results'", async () => {
    m.searchHfModels.mockResolvedValue(resp([result("nobody/A-9B", { role: "conversion", trust: unknown })]));
    await searchFor("a 9b");
    await screen.findByText("nobody/A-9B");
    await userEvent.click(screen.getByRole("button", { name: "Trusted publishers only" }));
    expect(screen.getByText(/The filters hide all 1 results/)).toBeInTheDocument();
  });

  it("choosing what it is for searches again with that purpose", async () => {
    m.searchHfModels.mockResolvedValue(resp([result("a/B-7B")]));
    await searchFor("b 7b");
    await screen.findByText("a/B-7B");
    await userEvent.click(screen.getByRole("button", { name: "Coding" }));
    await waitFor(() => expect(m.searchHfModels).toHaveBeenLastCalledWith("b 7b", "coding"));
    expect(screen.getByRole("button", { name: "Coding" })).toHaveAttribute("aria-pressed", "true");
  });

  it("explains how the query was understood, including a repaired typo and the quantization", async () => {
    m.searchHfModels.mockResolvedValue(resp([result("a/Qwen3.5-9B-GGUF")], {
      text: "qwen3.5 9b", size_b: 9, quant: "Q6_K_M", corrected_from: "qwwn3.5 9b",
    }));
    await searchFor("qwwn3.5-9B-Q6KM");
    expect(await screen.findByText(/you typed “qwwn3.5 9b”/)).toBeInTheDocument();
    expect(screen.getByText("Q6_K_M")).toBeInTheDocument();
  });

  it("opening files puts the typed quantization first and tags it; says so when none match", async () => {
    m.searchHfModels.mockResolvedValue(resp([result("a/Qwen-9B-GGUF")], { quant: "Q6_K_M" }));
    m.listHfFiles.mockResolvedValue([file("Q4.gguf", "Q4_K_M", 5e9), file("Q6.gguf", "Q6_K_M", 7e9)]);
    await searchFor("qwen 9b q6km");
    await userEvent.click(await screen.findByRole("button", { name: "Show files" }));
    expect(await screen.findByText("Matches your Q6_K_M")).toBeInTheDocument();
    const rows = Array.from(document.querySelectorAll(".browse-file-row"));
    expect(rows[0].textContent).toContain("Q6");
  });

  it("tells the person no file matches the typed quantization", async () => {
    m.searchHfModels.mockResolvedValue(resp([result("a/Qwen-9B-GGUF")], { quant: "Q6_K_M" }));
    m.listHfFiles.mockResolvedValue([file("Q4.gguf", "Q4_K_M", 5e9)]);
    await searchFor("qwen 9b q6km");
    await userEvent.click(await screen.findByRole("button", { name: "Show files" }));
    expect(await screen.findByText(/No file here is Q6_K_M/)).toBeInTheDocument();
  });

  it("shows the name-based size estimate with a caveat", async () => {
    m.searchHfModels.mockResolvedValue(resp([
      result("a/B-9B", { fit_hint: { approx_q4_bytes: 5.4e9, label: "WARNING", target: "gpu" } }),
    ]));
    await searchFor("b 9b");
    const badge = await screen.findByText(/at Q4/);
    expect(badge.getAttribute("title")).toMatch(/Estimated from the parameter count/);
  });

  it("the download folder is a labelled bar with a choose button", async () => {
    render(<OnlineModelSearch />);
    expect(await screen.findByText("Save downloads to")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose folder…" })).toBeInTheDocument();
  });
});

