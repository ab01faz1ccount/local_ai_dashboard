import type { VerificationStatus } from "../api";

/**
 * Square LED + label, using the same `data-state`-driven pattern as the
 * dashboard's `.status-led` (see RuntimeCard) rather than a new visual
 * vocabulary -- verification state should read as the same kind of fact
 * as a runtime's ONLINE/OFFLINE state. Colors are wired in styles.css
 * under `.verify-led[data-state="..."]`.
 */
const STATUS_LABELS: Record<NonNullable<VerificationStatus> | "unlinked", string> = {
  unlinked: "Not linked",
  pending: "Verifying…",
  verified: "Verified",
  hash_mismatch: "Hash mismatch",
  metadata_only: "Metadata match only",
  metadata_mismatch: "Metadata mismatch",
  not_found: "Not found on HF",
  no_hash_available: "No hash available",
  error: "Verification error",
};

export function VerificationBadge({ status }: { status: VerificationStatus }) {
  const state = status ?? "unlinked";
  return (
    <span className="verify-led" data-state={state}>
      {STATUS_LABELS[state]}
    </span>
  );
}
