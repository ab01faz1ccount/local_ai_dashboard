import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VerificationBadge } from "../src/components/VerificationBadge";

describe("VerificationBadge", () => {
  it("says how it was verified and explains it on hover", () => {
    render(<VerificationBadge status="verified" details={{ status: "verified" }} />);
    const led = screen.getByText("Verified by hash");
    expect(led).toHaveAttribute("data-state", "verified");
    expect(led).toHaveAttribute("data-level", "hash");
    expect(led.getAttribute("title")).toMatch(/does not vouch for who published it/);
  });

  it("shows a size-only match as its own state", () => {
    render(<VerificationBadge status="no_hash_available" details={{ status: "metadata_only" }} />);
    expect(screen.getByText("Size match only")).toHaveAttribute("data-state", "metadata_only");
  });

  it("shows a mismatch as an error colour", () => {
    render(<VerificationBadge status="hash_mismatch" details={{}} />);
    expect(screen.getByText("Doesn't match Hugging Face")).toHaveAttribute("data-state", "hash_mismatch");
  });

  it("an unchecked model reads Not verified", () => {
    render(<VerificationBadge status={null} />);
    expect(screen.getByText("Not verified")).toHaveAttribute("data-state", "unlinked");
  });

  it("showReason prints the explanation under the label", () => {
    render(<VerificationBadge status={null} showReason />);
    expect(screen.getByText(/never been compared with Hugging Face/)).toBeInTheDocument();
  });

  it("hides the explanation line by default", () => {
    render(<VerificationBadge status={null} />);
    expect(screen.queryByText(/never been compared/)).toBeNull();
  });
});
