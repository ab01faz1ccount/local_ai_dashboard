/**
 * frontend/src/gauge-utils.ts
 *
 * Geometry and thresholds for the semicircle gauges. Pure, so the arc maths
 * (the part that is easy to get subtly wrong) is tested without a browser.
 * The arc runs from the left end, over the top, to the right end.
 */

export type GaugeTone = "ok" | "warn" | "hot";

/** 0-100, or null for "no reading" (NaN, Infinity, null, undefined). */
export function clampPercent(v: number | null | undefined): number | null {
  if (v == null || !Number.isFinite(v)) return null;
  return Math.min(100, Math.max(0, v));
}

/** A point on the gauge circle for a percentage: 0 -> left end, 50 -> top, 100 -> right end. */
export function gaugePoint(cx: number, cy: number, r: number, percent: number): { x: number; y: number } {
  const angle = Math.PI - (percent / 100) * Math.PI;
  return { x: cx + r * Math.cos(angle), y: cy - r * Math.sin(angle) };
}

const f = (n: number) => n.toFixed(2);

/** SVG path for the arc from 0% to `percent`; "" when there is nothing to draw. */
export function gaugeArc(cx: number, cy: number, r: number, percent: number): string {
  const p = clampPercent(percent);
  if (p == null || p <= 0) return "";
  const start = gaugePoint(cx, cy, r, 0);
  const end = gaugePoint(cx, cy, r, p);
  // a semicircle is at most 180 degrees, so the large-arc flag is always 0;
  // sweep 1 = clockwise on screen = left, over the top, to the right
  return `M ${f(start.x)} ${f(start.y)} A ${f(r)} ${f(r)} 0 0 1 ${f(end.x)} ${f(end.y)}`;
}

/** How worried to look: used for the colour of the value, never as the only signal. */
export function gaugeTone(percent: number | null, warnAt = 75, hotAt = 90): GaugeTone {
  if (percent == null) return "ok";
  if (percent >= hotAt) return "hot";
  if (percent >= warnAt) return "warn";
  return "ok";
}

export function percentOf(used: number | null | undefined, total: number | null | undefined): number | null {
  if (used == null || total == null || !Number.isFinite(used) || !Number.isFinite(total) || total <= 0) return null;
  return clampPercent((used / total) * 100);
}
