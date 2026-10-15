import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ModeSwitcher } from "../src/components/ModeSwitcher";

describe("ModeSwitcher", () => {
  it("shows two circles, marks the current one and reports a change", async () => {
    const onChange = vi.fn();
    render(<ModeSwitcher value="dark" onChange={onChange} />);
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(2);
    expect(screen.getByRole("radio", { name: "Soft dark background" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "Soft light background" })).toHaveAttribute("aria-checked", "false");
    await userEvent.click(screen.getByRole("radio", { name: "Soft light background" }));
    expect(onChange).toHaveBeenCalledWith("light");
  });
  it("is its own radio group, apart from the colour palette one", () => {
    render(<ModeSwitcher value="light" onChange={() => undefined} />);
    expect(screen.getByRole("radiogroup", { name: "Background" })).toBeInTheDocument();
  });
});
