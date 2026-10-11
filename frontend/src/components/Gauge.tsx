import { useId } from "react";
import { clampPercent, gaugeArc, gaugeTone } from "../gauge-utils";

interface Props {
  /** 0-100, or null when there is no reading (no GPU, not sampled yet). */
  value: number | null | undefined;
  label: string;
  /** "lg" for the headline gauge, "sm" for the supporting ones. */
  size?: "lg" | "sm";
  /** One quiet line under the label: "12.4 / 31.8 GB", a GPU name, ... */
  detail?: string;
  /** Text shown instead of a number when there is no reading. */
  emptyText?: string;
}

// One drawing for every size; CSS scales it. The arc sits on a circle of
// radius R around (CX, CY); the round caps stick out by half the stroke.
const CX = 100;
const CY = 100;
const R = 84;
const STROKE = 16;

/** A semicircle gauge: a faint track and a gradient arc from the left to the value. */
export function Gauge({ value, label, size = "sm", detail, emptyText = "N/A" }: Props) {
  const gradientId = useId();
  const v = clampPercent(value);
  const tone = gaugeTone(v);
  const arc = v == null ? "" : gaugeArc(CX, CY, R, v);

  return (
    <div
      className={`gauge gauge-${size}`}
      data-tone={tone}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={v == null ? undefined : Math.round(v)}
      aria-valuetext={v == null ? "No reading" : `${Math.round(v)} percent`}
    >
      <div className="gauge-dial">
        <svg viewBox="0 0 200 110" aria-hidden="true" focusable="false">
          <defs>
            <linearGradient id={gradientId} gradientUnits="userSpaceOnUse" x1={CX - R} y1="0" x2={CX + R} y2="0">
              <stop offset="0" className="gauge-stop-from" />
              <stop offset="1" className="gauge-stop-to" />
            </linearGradient>
          </defs>
          <path d={gaugeArc(CX, CY, R, 100)} className="gauge-track" strokeWidth={STROKE} fill="none" strokeLinecap="round" />
          {arc && (
            <path d={arc} stroke={`url(#${gradientId})`} strokeWidth={STROKE} fill="none" strokeLinecap="round" />
          )}
        </svg>
        <div className="gauge-readout">
          {v == null ? (
            <span className="gauge-empty">{emptyText}</span>
          ) : (
            <>
              <span className="gauge-number">{Math.round(v)}</span>
              <span className="gauge-unit">%</span>
            </>
          )}
        </div>
      </div>
      <div className="gauge-label">{label}</div>
      {detail && <div className="gauge-detail">{detail}</div>}
    </div>
  );
}
