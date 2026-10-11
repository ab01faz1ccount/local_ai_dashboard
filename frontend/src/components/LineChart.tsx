import { useId, useState, type PointerEvent } from "react";
import {
  buildAreaPath,
  buildChartPath,
  formatMetric,
  hasGaps,
  nearestPointIndex,
  pointPosition,
  seriesStats,
  type ChartPoint,
} from "../analytics-utils";

interface Props {
  title: string;
  points: ChartPoint[];
  unit: string;
  /** Pin the top of the y axis (e.g. 100 for percent metrics). */
  fixedMax?: number;
  digits?: number;
}

const WIDTH = 320;
const HEIGHT = 90;
const PAD = 4;

const clock = (t: number) => new Date(t).toLocaleTimeString(undefined, { hour12: false });

/** A small dependency-free SVG line chart with min/avg/max/last readout and a
 * hover crosshair. An empty series renders an explicit "no data" state rather
 * than a blank box, since "nothing was sampled" (runtime offline, no GPU) is a
 * real answer the user should see. */
export function LineChart({ title, points, unit, fixedMax, digits = 1 }: Props) {
  const gradientId = useId();
  const [hover, setHover] = useState<number | null>(null);
  const stats = seriesStats(points);
  const geo = buildChartPath(points, WIDTH, HEIGHT, PAD, fixedMax);
  const area = buildAreaPath(points, WIDTH, HEIGHT, PAD, fixedMax);
  const marker = hover != null ? pointPosition(points, hover, WIDTH, HEIGHT, PAD, fixedMax) : null;

  function onMove(e: PointerEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    if (rect.width <= 0) return;
    const i = nearestPointIndex(points, ((e.clientX - rect.left) / rect.width) * WIDTH, WIDTH, PAD);
    setHover(i >= 0 ? i : null);
  }

  const shown = hover != null ? points[hover] : null;

  return (
    <div className="panel line-chart" data-testid={`chart-${title}`}>
      <div className="line-chart-header">
        <span className="line-chart-title">{title}</span>
        <span className="mono line-chart-last">{stats ? formatMetric(shown ? shown.v : stats.last, unit, digits) : "—"}</span>
      </div>
      {stats ? (
        <>
          <svg
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            className="line-chart-svg"
            role="img"
            aria-label={`${title} over time`}
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
          >
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" className="line-chart-wash-top" />
                <stop offset="1" className="line-chart-wash-bottom" />
              </linearGradient>
            </defs>
            <line x1="0" y1={HEIGHT - PAD} x2={WIDTH} y2={HEIGHT - PAD} className="line-chart-axis" />
            {area && <path d={area} fill={`url(#${gradientId})`} stroke="none" />}
            <path d={geo.path} className="line-chart-line" fill="none" />
            {marker && (
              <g>
                <line x1={marker.x} y1={PAD} x2={marker.x} y2={HEIGHT - PAD} className="line-chart-cross" />
                <circle cx={marker.x} cy={marker.y} r="4" className="line-chart-dot" />
              </g>
            )}
          </svg>
          <div className="muted mono line-chart-stats">
            {shown ? (
              <>
                {clock(shown.t)} · {formatMetric(shown.v, unit, digits)}
              </>
            ) : (
              <>
                min {formatMetric(stats.min, unit, digits)} · avg {formatMetric(stats.avg, unit, digits)} · max{" "}
                {formatMetric(stats.max, unit, digits)}
              </>
            )}
          </div>
          {hasGaps(points) && <div className="muted line-chart-note">Gaps in the data — the runtime was offline or idle.</div>}
        </>
      ) : (
        <div className="muted line-chart-empty">No data in this range.</div>
      )}
    </div>
  );
}
