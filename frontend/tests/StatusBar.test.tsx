import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StatusBar } from "../src/components/StatusBar";
import { bucketActivity } from "../src/status-bar-utils";

const NOW = Date.parse("2026-01-01T12:00:00Z");
const HOUR = 3_600_000;
const buckets = bucketActivity(
  [
    { timestamp: new Date(NOW - HOUR + 1000).toISOString(), tokens_per_sec: 40 },
    { timestamp: new Date(NOW - HOUR / 2).toISOString(), tokens_per_sec: null },
  ],
  HOUR,
  NOW,
  4
);

describe("StatusBar", () => {
  it("legend counts every state, so colour is never the only channel", () => {
    render(<StatusBar buckets={buckets} label="Runtime activity" />);
    const region = screen.getByRole("region", { name: "Runtime activity" });
    const legend = region.querySelector(".status-bar-legend") as HTMLElement;
    expect(legend).toHaveTextContent(/Generating\s*1/);
    expect(legend).toHaveTextContent(/Online, idle\s*1/);
    expect(legend).toHaveTextContent(/No data\s*2/);
  });
  it("hovering a slot names its status and sample count; leaving restores the range", async () => {
    render(<StatusBar buckets={buckets} label="Runtime activity" />);
    const cells = screen.getByTestId("status-bar-track").children;
    await userEvent.hover(cells[0]);
    expect(screen.getByText(/Generating · 1 samples/)).toBeInTheDocument();
    await userEvent.unhover(cells[0]);
    expect(screen.queryByText(/Generating · 1 samples/)).toBeNull();
  });
  it("a hidden table lists every slot for screen readers", () => {
    render(<StatusBar buckets={buckets} label="Runtime activity" />);
    expect(screen.getByRole("table", { hidden: true })).toBeInTheDocument();
    expect(screen.getAllByRole("row", { hidden: true })).toHaveLength(1 + buckets.length);
  });
});
