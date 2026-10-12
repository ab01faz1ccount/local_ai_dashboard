import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BumpChart } from "../src/components/BumpChart";
import { RankingsPanel } from "../src/components/RankingsPanel";
import { buildBump, makeBuckets } from "../src/bump-utils";

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  return { ...actual, api: { listRuntimes: vi.fn(), listAgents: vi.fn(), getRuntimeMetricsHistory: vi.fn(), getUsageHistory: vi.fn() } };
});
import { api } from "../src/api";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const entry = (id: string, values: (number | null)[]) => ({ id, label: id, values });
const buckets = makeBuckets(0, 300, 3);
const labels = ["a", "b", "c"];

describe("BumpChart", () => {
  const model = buildBump([entry("fast", [9, 9, 2]), entry("steady", [5, 5, 5]), entry("gappy", [null, 1, 1])], "higher");
  const view = () =>
    render(<BumpChart title="LLMs by speed" model={model} buckets={buckets} bucketLabels={labels} unit=" t/s" digits={1} rankMeaning="1 = highest" />);

  it("names every line at its end and in the legend", () => {
    view();
    for (const id of ["fast", "steady", "gappy"]) {
      expect(screen.getAllByText(id).length).toBeGreaterThanOrEqual(2); // end label + legend (+ table)
    }
  });

  it("describes itself for screen readers", () => {
    view();
    expect(screen.getByRole("img", { name: /3 entries ranked over 3 periods/ })).toBeInTheDocument();
  });

  it("hovering a legend item dims every other line, and leaving restores them", async () => {
    const { container } = view();
    await userEvent.hover(screen.getByRole("button", { name: "fast" }));
    expect(container.querySelector('[data-series="fast"]')!.getAttribute("data-dim")).toBe("false");
    expect(container.querySelector('[data-series="steady"]')!.getAttribute("data-dim")).toBe("true");
    await userEvent.unhover(screen.getByRole("button", { name: "fast" }));
    expect(container.querySelector('[data-series="steady"]')!.getAttribute("data-dim")).toBe("false");
  });

  it("clicking a legend item pins the highlight until clicked again", async () => {
    const { container } = view();
    const btn = screen.getByRole("button", { name: "fast" });
    await userEvent.click(btn);
    await userEvent.unhover(btn);
    expect(btn).toHaveAttribute("aria-pressed", "true");
    expect(container.querySelector('[data-series="steady"]')!.getAttribute("data-dim")).toBe("true");
    await userEvent.click(btn);
    expect(container.querySelector('[data-series="steady"]')!.getAttribute("data-dim")).toBe("false");
  });

  it("hovering a dot reads out its rank, value and period", async () => {
    const { container } = view();
    const hit = container.querySelector('[data-series="fast"] .bump-hit')!;
    await userEvent.hover(hit);
    expect(screen.getByText("fast — rank 1 · 9.0 t/s · a")).toBeInTheDocument();
  });

  it("the table has the real numbers and a dash where nothing was measured", () => {
    view();
    const row = screen.getByRole("row", { name: /gappy/ });
    expect(within(row).getByText("—")).toBeInTheDocument();
    expect(within(row).getAllByText(/#3 · 1\.0 t\/s/).length).toBe(2);
  });

  it("says so when more entries exist than are shown", () => {
    const many = buildBump(Array.from({ length: 10 }, (_, i) => entry(`s${i}`, [10 - i, 10 - i, 10 - i])), "higher");
    render(<BumpChart title="t" model={many} buckets={buckets} bucketLabels={labels} unit="" digits={0} rankMeaning="" />);
    expect(screen.getByText(/Showing 8 of 10/)).toBeInTheDocument();
  });

  it("an empty model explains itself instead of drawing an empty chart", () => {
    render(<BumpChart title="t" model={buildBump([], "higher")} buckets={buckets} bucketLabels={labels} unit="" digits={0} rankMeaning="" />);
    expect(screen.getByText(/nothing to rank yet/)).toBeInTheDocument();
  });
});

describe("RankingsPanel", () => {
  const snap = (id: number, tps: number) => ({
    id: 1, runtime_id: id, timestamp: new Date(Date.now() - 5 * 60_000).toISOString(), cpu_percent: null, ram_used_mb: null, ram_total_mb: null,
    gpu_percent: null, vram_used_mb: null, vram_total_mb: null, tokens_per_sec: tps, last_latency_ms: 100,
  });

  beforeEach(() => {
    Object.values(m).forEach((f) => f.mockReset());
    m.listRuntimes.mockResolvedValue([
      { id: 1, name: "r1", model_name: "Qwen" },
      { id: 2, name: "r2", model_name: "Llama" },
    ]);
    m.getRuntimeMetricsHistory.mockImplementation(async (id: number) => [snap(id, id === 1 ? 20 : 50)]);
    m.listAgents.mockResolvedValue([{ id: 7, name: "Hermes" }]);
    m.getUsageHistory.mockResolvedValue([
      { id: 1, runtime_id: 1, agent_id: 7, started_at: new Date(Date.now() - 3600_000).toISOString(), ended_at: null, status: "ACTIVE", requests_count: 4, avg_latency_ms: 90, avg_tokens_per_sec: 30 },
    ]);
  });

  it("ranks the runtimes by their own measured speed", async () => {
    render(<RankingsPanel />);
    expect(await screen.findByText("LLMs by speed (tokens/sec)")).toBeInTheDocument();
    const series = document.querySelectorAll("[data-series]");
    expect(series.length).toBe(2);
    expect(m.getRuntimeMetricsHistory).toHaveBeenCalledTimes(2);
    expect(m.getRuntimeMetricsHistory.mock.calls[0][1].limit).toBe(5000);
  });

  it("switching the metric to latency re-ranks with 1 = lowest", async () => {
    render(<RankingsPanel />);
    await screen.findByText("LLMs by speed (tokens/sec)");
    await userEvent.selectOptions(screen.getByLabelText("Rank by"), "last_latency_ms");
    expect(await screen.findByText("1 = lowest")).toBeInTheDocument();
  });

  it("the Agents tab ranks agents from their recorded sessions", async () => {
    render(<RankingsPanel />);
    await screen.findByText("LLMs by speed (tokens/sec)");
    await userEvent.click(screen.getByRole("button", { name: "Agents" }));
    expect(await screen.findByText("Agents by requests")).toBeInTheDocument();
    expect(m.getUsageHistory).toHaveBeenCalledWith({ limit: 1000 });
    await waitFor(() => expect(document.querySelector('[data-series="agent:7"]')).not.toBeNull());
  });

  it("no runtimes is an empty state, not a blank chart", async () => {
    m.listRuntimes.mockResolvedValue([]);
    render(<RankingsPanel />);
    expect(await screen.findByText("No runtimes yet.")).toBeInTheDocument();
  });

  it("a failed load shows an error", async () => {
    m.listRuntimes.mockRejectedValue(new Error(JSON.stringify({ detail: "backend down" })));
    render(<RankingsPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("backend down");
  });
});
