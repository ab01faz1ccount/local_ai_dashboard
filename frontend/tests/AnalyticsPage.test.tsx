import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AnalyticsPage } from "../src/AnalyticsPage";
import type { MetricsSnapshotItem } from "../src/api";

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  return { ...actual, api: { listRuntimes: vi.fn(), getRuntimeMetricsHistory: vi.fn() } };
});
import { api } from "../src/api";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const rt = (id: number, name: string) => ({ id, name });
const snap = (o: Partial<MetricsSnapshotItem> = {}): MetricsSnapshotItem => ({
  id: 1, runtime_id: 1, timestamp: "2026-01-01T00:00:00Z", cpu_percent: 40, ram_used_mb: 4000, ram_total_mb: 16000,
  gpu_percent: null, vram_used_mb: null, vram_total_mb: null, tokens_per_sec: 25, last_latency_ms: 120, ...o,
});

beforeEach(() => {
  Object.values(m).forEach((f) => f.mockReset());
  m.listRuntimes.mockResolvedValue([rt(1, "llama-a"), rt(2, "llama-b")]);
  m.getRuntimeMetricsHistory.mockResolvedValue([]);
});

describe("AnalyticsPage", () => {
  it("loads history for the first runtime with the default 1h range", async () => {
    render(<AnalyticsPage />);
    await waitFor(() => expect(m.getRuntimeMetricsHistory).toHaveBeenCalled());
    const [id, opts] = m.getRuntimeMetricsHistory.mock.calls[0];
    expect(id).toBe(1);
    expect(opts.limit).toBe(2000);
    const sinceMs = Date.now() - Date.parse(opts.since);
    expect(sinceMs).toBeGreaterThan(59 * 60_000);
    expect(sinceMs).toBeLessThan(61 * 60_000);
  });

  it("renders all six charts", async () => {
    m.getRuntimeMetricsHistory.mockResolvedValue([snap()]);
    render(<AnalyticsPage />);
    for (const t of ["Tokens / sec", "Latency", "CPU", "RAM", "GPU", "VRAM"]) {
      expect(await screen.findByTestId(`chart-${t}`)).toBeInTheDocument();
    }
  });

  it("plots real values and shows no-data for a metric the machine doesn't have", async () => {
    m.getRuntimeMetricsHistory.mockResolvedValue([snap()]);
    render(<AnalyticsPage />);
    const tps = await screen.findByTestId("chart-Tokens / sec");
    expect(within(tps).getByText("25.0 t/s")).toBeInTheDocument();
    expect(within(screen.getByTestId("chart-RAM")).getByText("25.0%")).toBeInTheDocument(); // 4000/16000
    expect(within(screen.getByTestId("chart-GPU")).getByText("No data in this range.")).toBeInTheDocument();
  });

  it("changing the range refetches with a different since", async () => {
    render(<AnalyticsPage />);
    await waitFor(() => expect(m.getRuntimeMetricsHistory).toHaveBeenCalledTimes(1));
    const first = m.getRuntimeMetricsHistory.mock.calls[0][1].since;
    await userEvent.click(screen.getByRole("button", { name: "24h" }));
    await waitFor(() => expect(m.getRuntimeMetricsHistory).toHaveBeenCalledTimes(2));
    expect(Date.parse(m.getRuntimeMetricsHistory.mock.calls[1][1].since)).toBeLessThan(Date.parse(first));
    expect(screen.getByRole("button", { name: "24h" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "1h" })).toHaveAttribute("aria-pressed", "false");
  });

  it("switching runtime refetches for that runtime", async () => {
    render(<AnalyticsPage />);
    await waitFor(() => expect(m.getRuntimeMetricsHistory).toHaveBeenCalledTimes(1));
    await userEvent.selectOptions(screen.getByLabelText("Runtime"), "2");
    await waitFor(() => expect(m.getRuntimeMetricsHistory.mock.calls.at(-1)![0]).toBe(2));
  });

  it("no runtimes -> an explanatory empty state and no history call", async () => {
    m.listRuntimes.mockResolvedValue([]);
    render(<AnalyticsPage />);
    expect(await screen.findByText(/No runtimes yet/)).toBeInTheDocument();
    expect(m.getRuntimeMetricsHistory).not.toHaveBeenCalled();
  });

  it("shows an error when history fails to load", async () => {
    m.getRuntimeMetricsHistory.mockRejectedValue(new Error(JSON.stringify({ detail: "db locked" })));
    render(<AnalyticsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("db locked");
  });

  it("auto-refreshes on an interval", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<AnalyticsPage />);
      await waitFor(() => expect(m.getRuntimeMetricsHistory).toHaveBeenCalledTimes(1));
      await vi.advanceTimersByTimeAsync(15_100);
      expect(m.getRuntimeMetricsHistory.mock.calls.length).toBeGreaterThanOrEqual(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
