import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { LineChart } from "../src/components/LineChart";

describe("LineChart", () => {
  it("empty series shows an explicit no-data state, not a blank box", () => {
    render(<LineChart title="GPU" points={[]} unit="%" />);
    expect(screen.getByText("No data in this range.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("draws the line and shows last + min/avg/max", () => {
    const pts = [{ t: 0, v: 10 }, { t: 1000, v: 30 }, { t: 2000, v: 20 }];
    render(<LineChart title="CPU" points={pts} unit="%" />);
    expect(screen.getByRole("img", { name: "CPU over time" })).toBeInTheDocument();
    expect(screen.getByText("20.0%")).toBeInTheDocument(); // last
    expect(screen.getByText(/min 10\.0% · avg 20\.0% · max 30\.0%/)).toBeInTheDocument();
  });

  it("notes gaps in the data", () => {
    const pts = [0, 1, 2, 3, 4].map((i) => ({ t: i * 15_000, v: 1 })).concat([{ t: 4 * 15_000 + 3_600_000, v: 1 }]);
    render(<LineChart title="Latency" points={pts} unit=" ms" />);
    expect(screen.getByText(/Gaps in the data/)).toBeInTheDocument();
  });

  it("no gap note for evenly spaced data", () => {
    const pts = [0, 1, 2, 3, 4].map((i) => ({ t: i * 15_000, v: 1 }));
    render(<LineChart title="Latency" points={pts} unit=" ms" />);
    expect(screen.queryByText(/Gaps in the data/)).not.toBeInTheDocument();
  });

  it("hovering shows the value under the pointer instead of min/avg/max, leaving restores it", () => {
    const pts = [{ t: 0, v: 10 }, { t: 1000, v: 30 }, { t: 2000, v: 20 }];
    render(<LineChart title="CPU" points={pts} unit="%" />);
    const svg = screen.getByRole("img", { name: "CPU over time" });
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 320, height: 90, right: 320, bottom: 90, x: 0, y: 0, toJSON() {} }) as DOMRect;
    fireEvent.pointerMove(svg, { clientX: 160 }); // the middle -> the second point
    expect(screen.getByTestId("chart-CPU")).toHaveTextContent("30.0%");
    expect(screen.queryByText(/min 10\.0%/)).toBeNull();
    fireEvent.pointerLeave(svg);
    expect(screen.getByText(/min 10\.0% · avg 20\.0% · max 30\.0%/)).toBeInTheDocument();
  });
});
