import { describe, expect, it } from "vitest";
import { modelOptionLabel, trustInfo, trustMark } from "../src/verification-utils";

describe("trustInfo", () => {
  it("hash match is the only 'verified by hash'", () => {
    const t = trustInfo("verified", { status: "verified", reason: "SHA-256 of the downloaded file matched the hash Hugging Face publishes for it" });
    expect(t.level).toBe("hash");
    expect(t.label).toBe("Verified by hash");
    expect(t.ledState).toBe("verified");
    expect(t.explanation).toContain("SHA-256 of the downloaded file matched");
    // never implies the publisher is trusted
    expect(t.explanation).toContain("does not vouch for who published it");
  });

  it("falls back to a default sentence when the details have no reason", () => {
    expect(trustInfo("verified", {}).explanation).toContain("matches the hash Hugging Face publishes");
  });

  it("size-only is read from the details even though the stored column says no_hash_available", () => {
    const t = trustInfo("no_hash_available", { status: "metadata_only" });
    expect(t.level).toBe("size_only");
    expect(t.label).toBe("Size match only");
    expect(t.explanation).toMatch(/not a swapped file/);
  });

  it("a plain no_hash_available stays unverified", () => {
    const t = trustInfo("no_hash_available", { status: "no_hash_available" });
    expect(t.level).toBe("unverified");
    expect(trustInfo("no_hash_available", {}).level).toBe("unverified");
  });

  it("either mismatch is a warning, never a pass", () => {
    for (const s of ["hash_mismatch", "metadata_mismatch"] as const) {
      const t = trustInfo(s, {});
      expect(t.level).toBe("mismatch");
      expect(t.ledState).toBe("hash_mismatch");
      expect(t.explanation).toMatch(/Delete it and download/);
    }
  });

  it("pending wins over a stale earlier result in the details", () => {
    expect(trustInfo("pending", { status: "verified" }).level).toBe("pending");
  });

  it("an error shows its reason", () => {
    const t = trustInfo("error", { status: "error", reason: "couldn't read local file" });
    expect(t.level).toBe("error");
    expect(t.explanation).toContain("couldn't read local file");
    expect(trustInfo("error", {}).explanation).toMatch(/Try Verify again/);
  });

  it("never-checked (null / undefined / empty) is 'Not verified', not a pass", () => {
    for (const s of [null, undefined]) {
      const t = trustInfo(s as never, undefined);
      expect(t.level).toBe("unverified");
      expect(t.label).toBe("Not verified");
      expect(t.ledState).toBe("unlinked");
    }
  });

  it("not_found shows its own reason", () => {
    expect(trustInfo("not_found", { status: "not_found", reason: "o/r/x.gguf not found on Hugging Face" }).explanation).toContain("not found on Hugging Face");
  });
});

describe("dropdown marks", () => {
  const base = { name: "m.gguf" };
  it("only states that change what you should do get a mark", () => {
    expect(trustMark({ verification_status: "verified", verification_details: { status: "verified" } })).toBe(" ✓ hash-verified");
    expect(trustMark({ verification_status: "no_hash_available", verification_details: { status: "metadata_only" } })).toBe(" · size match only");
    expect(trustMark({ verification_status: "hash_mismatch", verification_details: {} })).toBe(" ⚠ does not match Hugging Face");
    expect(trustMark({ verification_status: null, verification_details: {} })).toBe("");
    expect(trustMark({ verification_status: "pending", verification_details: {} })).toBe("");
    expect(trustMark({ verification_status: "error", verification_details: {} })).toBe("");
  });

  it("keeps the plain name for unverified models and tolerates missing fields", () => {
    expect(modelOptionLabel({ ...base, verification_status: null, verification_details: {} })).toBe("m.gguf");
    expect(modelOptionLabel({ ...base } as never)).toBe("m.gguf");
  });

  it("adds the mark after the name", () => {
    expect(modelOptionLabel({ ...base, verification_status: "verified", verification_details: { status: "verified" } })).toBe("m.gguf ✓ hash-verified");
  });
});
