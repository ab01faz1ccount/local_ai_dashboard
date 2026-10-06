import type { HardwareFit } from "../api";
import { FIT_TEXT, FIT_TONE } from "../context-utils";

/** The Hardware Fit Analyzer's verdict as a colored badge. The reason is
 * the title (hover) rather than inline: the table cell stays scannable,
 * but "why is this Tight?" is one hover away. The text label is always
 * shown, so color is never the only signal. */
export function FitBadge({ fit }: { fit: HardwareFit }) {
  const where = fit.target === "gpu" ? " · GPU" : fit.target === "cpu" ? " · CPU" : "";
  return (
    <span className="fit-badge" data-tone={FIT_TONE[fit.label]} data-fit={fit.label} title={fit.reason}>
      {FIT_TEXT[fit.label]}
      {where}
    </span>
  );
}
