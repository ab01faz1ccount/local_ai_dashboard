import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { ModelCompareTable } from "../src/components/ModelCompareTable";
import type { HardwareFit, ModelComparisonRow } from "../src/api";

const row = (id: number, name: string, fit: Partial<HardwareFit>): ModelComparisonRow => ({
  metadata: { id, name, file_size_bytes: 4e9, quantization: "Q4_K_M", param_count: "7B", context_length: 4096, hf_repo_id: null },
  estimated_system_impact: { estimated_base_mb: 3800, estimated_kv_cache_mb: 300, estimated_total_mb: 4100, note: "n" },
  hardware_fit: { label: "GOOD", target: "gpu", ratio: 0.5, capacity_mb: 8192, estimated_mb: 4100, reason: "r", ...fit },
  real_world_performance: { requests_count: 0, avg_latency_ms: null, avg_tokens_per_sec: null },
  public_benchmarks: null,
});

describe("ModelCompareTable fit row", () => {
  it("shows one fit badge per model, in column order", () => {
    render(<ModelCompareTable internetAvailable={false} rows={[row(1, "small", {}), row(2, "huge", { label: "NOT_RECOMMENDED", target: "cpu" })]} />);
    const tr = screen.getByText("Fit on this machine").closest("tr")!;
    const badges = within(tr).getAllByTitle("r");
    expect(badges).toHaveLength(2);
    expect(badges[0]).toHaveAttribute("data-fit", "GOOD");
    expect(badges[1]).toHaveAttribute("data-fit", "NOT_RECOMMENDED");
  });

  it("sits with the estimated figures, not the observed ones", () => {
    render(<ModelCompareTable internetAvailable={false} rows={[row(1, "m", {})]} />);
    const rows = screen.getAllByRole("row").map((r) => r.textContent ?? "");
    const est = rows.findIndex((t) => t.includes("Est. total RAM/VRAM"));
    const fit = rows.findIndex((t) => t.includes("Fit on this machine"));
    const observed = rows.findIndex((t) => t.includes("Real-world performance"));
    expect(fit).toBe(est + 1);
    expect(fit).toBeLessThan(observed);
  });
});
