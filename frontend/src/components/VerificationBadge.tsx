import type { VerificationDetails, VerificationStatus } from "../api";
import { trustInfo } from "../verification-utils";

/**
 * Square LED + label, using the same `data-state`-driven pattern as the
 * dashboard's `.status-led` (see RuntimeCard) rather than a new visual
 * vocabulary -- verification state should read as the same kind of fact
 * as a runtime's ONLINE/OFFLINE state. Colors are wired in styles.css
 * under `.verify-led[data-state="..."]`.
 *
 * The wording comes from verification-utils.ts so every list says the same
 * thing. `showReason` adds a short line under the label explaining what the
 * state proves and what it does not.
 */
export function VerificationBadge({
  status,
  details,
  showReason = false,
}: {
  status: VerificationStatus;
  details?: VerificationDetails | null;
  showReason?: boolean;
}) {
  const info = trustInfo(status, details);
  return (
    <span className="verify-badge">
      <span className="verify-led" data-state={info.ledState} data-level={info.level} title={info.explanation}>
        {info.label}
      </span>
      {showReason && <span className="verify-reason">{info.explanation}</span>}
    </span>
  );
}
