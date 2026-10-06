import { buildChartPath, formatMetric, hasGaps, seriesStats, type ChartPoint } from "../analytics-utils";

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

/** A small dependency-free SVG line chart with min/avg/max/last readout.
 * An empty series renders an explicit "no data" state rather than a blank
 * box, since "nothing was sampled" (runtime offline, no GPU) is a real
 * answer the user should see. */
export function LineChart({ title, points, unit, fixedMax, digits = 1 }: Props) {
  const stats = seriesStats(points);
  const geo = buildChartPath(points, WIDTH, HEIGHT, 4, fixedMax);

  return (
    <div className="panel line-chart" data-testid={`chart-${title}`}>
      <div className="line-chart-header">
        <span className="line-chart-title">{title}</span>
        <span className="mono line-chart-last">{stats ? formatMetric(stats.last, unit, digits) : "—"}</span>
      </div>
      {stats ? (
        <>
          <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="line-chart-svg" role="img" aria-label={`${title} over time`}>
            <line x1="0" y1={HEIGHT - 4} x2={WIDTH} y2={HEIGHT - 4} className="line-chart-axis" />
            <path d={geo.path} className="line-chart-line" fill="none" />
          </svg>
          <div className="muted mono line-chart-stats">
            min {formatMetric(stats.min, unit, digits)} · avg {formatMetric(stats.avg, unit, digits)} · max{" "}
            {formatMetric(stats.max, unit, digits)}
          </div>
          {hasGaps(points) && <div className="muted line-chart-note">Gaps in the data — the runtime was offline or idle.</div>}
        </>
      ) : (
        <div className="muted line-chart-empty">No data in this range.</div>
      )}
    </div>
  );
}
