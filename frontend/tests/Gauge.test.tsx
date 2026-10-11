import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Gauge } from "../src/components/Gauge";
import { HardwareStrip } from "../src/components/HardwareStrip";
import type { HardwareSnapshot } from "../src/api";

describe("Gauge", () => {
  it("is a meter that reads the rounded percentage", () => {
    render(<Gauge label="CPU" value={63.6} />);
    const m = screen.getByRole("meter", { name: "CPU" });
    expect(m).toHaveAttribute("aria-valuenow", "64");
    expect(m).toHaveAttribute("aria-valuetext", "64 percent");
    expect(screen.getByText("64")).toBeInTheDocument();
  });
  it("no reading shows the empty text and no value, never a fake 0", () => {
    render(<Gauge label="GPU" value={null} />);
    const m = screen.getByRole("meter", { name: "GPU" });
    expect(m).not.toHaveAttribute("aria-valuenow");
    expect(m).toHaveAttribute("aria-valuetext", "No reading");
    expect(screen.getByText("N/A")).toBeInTheDocument();
  });
  it("tone follows the load: calm, then warn at 75, hot at 90", () => {
    const { rerender } = render(<Gauge label="VRAM" value={50} />);
    expect(screen.getByRole("meter")).toHaveAttribute("data-tone", "ok");
    rerender(<Gauge label="VRAM" value={80} />);
    expect(screen.getByRole("meter")).toHaveAttribute("data-tone", "warn");
    rerender(<Gauge label="VRAM" value={95} />);
    expect(screen.getByRole("meter")).toHaveAttribute("data-tone", "hot");
  });
  it("out-of-range values are clamped", () => {
    render(<Gauge label="X" value={180} />);
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "100");
  });
});

const snap = (gpu: HardwareSnapshot["gpu"]): HardwareSnapshot => ({
  cpu: { percent: 12, core_count: 14, per_core_percent: null },
  memory: { used_mb: 8192, total_mb: 16384, percent: 50 },
  gpu,
  runtimes: [],
});

describe("HardwareStrip", () => {
  it("shows four gauges with their numbers when a GPU is present", () => {
    render(<HardwareStrip snapshot={snap({ available: true, name: "RTX 5060 Ti", utilization_percent: 30, vram_used_mb: 4096, vram_total_mb: 8192 })} />);
    expect(screen.getAllByRole("meter")).toHaveLength(4);
    expect(screen.getByRole("meter", { name: "VRAM" })).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getByText("RTX 5060 Ti")).toBeInTheDocument();
    expect(screen.getByText("14 cores")).toBeInTheDocument();
  });
  it("no GPU: GPU and VRAM say so instead of showing zero", () => {
    render(<HardwareStrip snapshot={snap({ available: false, name: null, utilization_percent: null, vram_used_mb: null, vram_total_mb: null })} />);
    expect(screen.getByRole("meter", { name: "GPU" })).toHaveAttribute("aria-valuetext", "No reading");
    expect(screen.getByRole("meter", { name: "VRAM" })).toHaveAttribute("aria-valuetext", "No reading");
    expect(screen.getByText("no NVIDIA GPU detected")).toBeInTheDocument();
  });
  it("before the first reading nothing is invented", () => {
    render(<HardwareStrip snapshot={null} />);
    for (const m of screen.getAllByRole("meter")) expect(m).toHaveAttribute("aria-valuetext", "No reading");
  });
});
