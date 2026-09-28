import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EventBadge } from "../src/components/EventBadge";

describe("EventBadge", () => {
  it("shows the event type as its own label", () => {
    render(<EventBadge eventType="runtime.started" />);
    expect(screen.getByText("runtime.started")).toBeInTheDocument();
  });

  it.each([
    ["runtime.started", "ok"],
    ["model.loaded", "ok"],
    ["agent.created", "ok"],
    ["model.verification_completed", "ok"],
    ["runtime.stopped", "muted"],
    ["model.unloaded", "muted"],
    ["agent.deleted", "muted"],
    ["runtime.crashed", "bad"],
    ["inference.failed", "bad"],
    ["permission.denied", "bad"],
    ["permission.requested", "pending"],
    ["runtime.metrics", "pending"],
    ["some.unknown.suffix", "muted"], // unrecognized suffix defaults to muted, not a crash
  ])("%s gets tone=%s", (eventType, tone) => {
    const { container } = render(<EventBadge eventType={eventType} />);
    expect(container.querySelector(".event-led")).toHaveAttribute("data-tone", tone);
  });
});
