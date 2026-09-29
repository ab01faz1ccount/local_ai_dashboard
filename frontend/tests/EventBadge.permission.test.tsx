import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { EventBadge } from "../src/components/EventBadge";

describe("EventBadge: permission.* types", () => {
  it.each([
    ["permission.requested", "pending"],
    ["permission.approved", "ok"],
    ["permission.denied", "bad"],
  ])("%s -> %s", (type, tone) => {
    render(<EventBadge eventType={type} />);
    expect(screen.getByText(type)).toHaveAttribute("data-tone", tone);
  });
});
