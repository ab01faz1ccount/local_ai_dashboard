import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FitBadge } from "../src/components/FitBadge";
import type { HardwareFit } from "../src/api";

const fit = (o: Partial<HardwareFit> = {}): HardwareFit => ({
  label: "GOOD", target: "gpu", ratio: 0.3, capacity_mb: 8192, estimated_mb: 2400, reason: "Estimated to use 30% of VRAM.", ...o,
});

describe("FitBadge", () => {
  it("shows the verdict text, where it was judged, and the reason on hover", () => {
    render(<FitBadge fit={fit()} />);
    const el = screen.getByText(/Good fit/);
    expect(el).toHaveTextContent("Good fit · GPU");
    expect(el).toHaveAttribute("title", "Estimated to use 30% of VRAM.");
    expect(el).toHaveAttribute("data-tone", "ok");
  });

  it.each([
    ["WARNING", "Tight fit", "warn"],
    ["LIKELY_TO_EXCEED", "Likely to exceed", "bad"],
    ["NOT_RECOMMENDED", "Not recommended", "bad"],
    ["UNKNOWN", "Unknown", "muted"],
  ] as const)("%s -> %s (%s)", (label, text, tone) => {
    render(<FitBadge fit={fit({ label, target: null })} />);
    const el = screen.getByText(text);
    expect(el).toHaveAttribute("data-tone", tone);
    expect(el).toHaveAttribute("data-fit", label);
  });

  it("CPU target is labeled", () => {
    render(<FitBadge fit={fit({ target: "cpu" })} />);
    expect(screen.getByText(/Good fit/)).toHaveTextContent("· CPU");
  });

  it("no target -> no location suffix", () => {
    render(<FitBadge fit={fit({ label: "UNKNOWN", target: null })} />);
    expect(screen.getByText("Unknown").textContent).toBe("Unknown");
  });
});
