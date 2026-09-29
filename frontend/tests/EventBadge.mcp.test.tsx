import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { EventBadge } from "../src/components/EventBadge";

describe("EventBadge: mcp.* types", () => {
  it.each([
    ["mcp.connected", "ok"], ["mcp.server_registered", "ok"],
    ["mcp.disconnected", "muted"], ["mcp.server_removed", "muted"],
    ["mcp.connect_failed", "bad"],
  ])("%s -> %s", (type, tone) => {
    render(<EventBadge eventType={type} />);
    expect(screen.getByText(type)).toHaveAttribute("data-tone", tone);
  });
});
