import type { ModelSummary, VerificationDetails, VerificationStatus } from "./api";

/**
 * How well a model file has been checked against Hugging Face, in plain words.
 *
 * The stored status is a coarse column; the exact result (including "only the
 * size matched") is in the details. This is the one place that turns both into
 * something a person can act on, so every list shows the same wording.
 *
 *   hash        the file's SHA-256 matched the hash Hugging Face publishes
 *   size_only   only the byte count matched (no hash was available)
 *   mismatch    it differs from Hugging Face's copy: corrupt or altered
 *   pending     a check is running
 *   error       the check itself failed
 *   unverified  never compared, or nothing to compare against
 */
export type TrustLevel = "hash" | "size_only" | "mismatch" | "pending" | "error" | "unverified";

export interface TrustInfo {
  level: TrustLevel;
  label: string;
  /** One or two sentences: what this proves, and what it does not. */
  explanation: string;
  /** The value `.verify-led[data-state]` is styled by. */
  ledState: string;
}

const NOT_A_PUBLISHER_ENDORSEMENT = "It confirms the file is intact and identical to Hugging Face's copy; it does not vouch for who published it.";

export function trustInfo(status: VerificationStatus | undefined, details?: VerificationDetails | null): TrustInfo {
  const s = status ?? null;
  if (s === "pending") {
    return { level: "pending", label: "Verifying…", explanation: "Comparing the file with Hugging Face now.", ledState: "pending" };
  }
  // The exact result lives in the details; fall back to the stored column.
  const exact = details?.status ?? s;
  const reason = details?.reason?.trim();

  switch (exact) {
    case "verified":
      return {
        level: "hash",
        label: "Verified by hash",
        explanation: `${reason ? `${reason}. ` : "The file's SHA-256 matches the hash Hugging Face publishes. "}${NOT_A_PUBLISHER_ENDORSEMENT}`,
        ledState: "verified",
      };
    case "metadata_only":
      return {
        level: "size_only",
        label: "Size match only",
        explanation:
          "Only the file size matched, because no hash was available. That catches a cut-off download but not a swapped file. Press Verify to try a hash check.",
        ledState: "metadata_only",
      };
    case "hash_mismatch":
    case "metadata_mismatch":
      return {
        level: "mismatch",
        label: "Doesn't match Hugging Face",
        explanation: "This file differs from the copy on Hugging Face, so it may be corrupt or altered. Delete it and download it again.",
        ledState: "hash_mismatch",
      };
    case "error":
      return {
        level: "error",
        label: "Could not verify",
        explanation: reason ? `The check failed: ${reason}.` : "The check failed. Try Verify again.",
        ledState: "error",
      };
    case "not_found":
      return {
        level: "unverified",
        label: "Not verified",
        explanation: reason ? `${reason}.` : "This file wasn't found on Hugging Face, so there is nothing to compare it with.",
        ledState: "unlinked",
      };
    case "no_hash_available":
      return {
        level: "unverified",
        label: "Not verified",
        explanation: "Hugging Face published neither a hash nor a size for this file, so nothing could be compared.",
        ledState: "unlinked",
      };
    default:
      return {
        level: "unverified",
        label: "Not verified",
        explanation: "This file has never been compared with Hugging Face. Link the model to its repository, then press Verify.",
        ledState: "unlinked",
      };
  }
}

/**
 * Text to append to a model's name inside a plain <option>. Only the states that
 * change what you should do get a mark: a tick for a hash check, a note for a
 * size-only check, a warning for a mismatch. Everything else adds nothing, so the
 * absence of a tick is itself the signal.
 */
export function trustMark(m: Pick<ModelSummary, "verification_status" | "verification_details">): string {
  switch (trustInfo(m.verification_status, m.verification_details).level) {
    case "hash":
      return " ✓ hash-verified";
    case "size_only":
      return " · size match only";
    case "mismatch":
      return " ⚠ does not match Hugging Face";
    default:
      return "";
  }
}

/** The name as shown in a dropdown. */
export function modelOptionLabel(m: Pick<ModelSummary, "name" | "verification_status" | "verification_details">): string {
  return `${m.name}${trustMark(m)}`;
}
