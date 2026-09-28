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
    m.searchHfModels.mockResolvedValue([result("acme/Cool-3B-GGUF"), result("zed/Cool-3B-Instruct", { gated: true })]);
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
    m.searchHfModels.mockResolvedValue([result("zed/Gated-7B", { gated: true })]);
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
    m.searchHfModels.mockResolvedValueOnce([]);
    render(<OnlineModelSearch />);
    await userEvent.type(screen.getByLabelText("Model name"), "zzz{Enter}");
    expect(await screen.findByText(/چیزی پیدا نشد/)).toBeInTheDocument();
    m.searchHfModels.mockResolvedValueOnce([result("a/b")]);
    m.listHfFiles.mockResolvedValueOnce([]);
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
    await userEvent.click(await screen.findByRole("button", { name: "Change…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Select this folder" }));
    expect(m.setModelsDir).toHaveBeenCalledWith("/mnt/big");
    expect(await screen.findByText("/mnt/big")).toBeInTheDocument();
  });
});
