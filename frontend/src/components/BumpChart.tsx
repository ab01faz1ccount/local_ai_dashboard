import { useMemo, useState } from "react";
import { bumpLayout, formatValue, type Bucket, type BumpModel } from "../bump-utils";

interface Props {
  title: string;
  model: BumpModel;
  buckets: Bucket[];
  /** Label per bucket (the x axis). */
  bucketLabels: string[];
  unit: string;
  digits: number;
  /** "Best = fastest" etc.: said once so the reader knows what rank 1 means. */
  rankMeaning: string;
}

const WIDTH = 720;
const ROW = 40;
const PAD = { left: 34, right: 150, top: 22, bottom: 30 };

/**
 * Rank over time: each line is one entry (an LLM runtime or an agent), the
 * vertical position is its rank in that time bucket (1 = best, at the top).
 * Lines that cross are the story: who overtook whom. A gap means "nothing was
 * measured then", never "last place".
 *
 * Identity is never color alone: every line is named at its right end, the
 * legend repeats the names, hovering or focusing either one highlights that
 * line, and the full numbers are one click away in the table.
 */
export function BumpChart({ title, model, buckets, bucketLabels, unit, digits, rankMeaning }: Props) {
  const [active, setActive] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  const [point, setPoint] = useState<{ id: string; bucket: number } | null>(null);
  const focus = pinned ?? active;

  const height = Math.max(model.maxRank, 1) * ROW + PAD.top + PAD.bottom;
  const layout = useMemo(
    () => bumpLayout(model.series, buckets.length, model.maxRank, WIDTH, height, PAD),
    [model.series, model.maxRank, buckets.length, height]
  );
  const byId = new Map(model.series.map((s) => [s.id, s]));
  const shown = point ? byId.get(point.id) : undefined;
  const shownValue = shown && point ? shown.values[point.bucket] : null;
  const shownRank = shown && point ? shown.ranks[point.bucket] : null;

  if (model.series.length === 0) {
    return (
      <div className="panel bump-chart" data-testid={`bump-${title}`}>
        <div className="line-chart-title">{title}</div>
        <p className="muted">Nothing was measured in this period, so there is nothing to rank yet.</p>
      </div>
    );
  }

  const dim = (id: string) => focus != null && focus !== id;

  return (
    <div className="panel bump-chart" data-testid={`bump-${title}`}>
      <div className="bump-header">
        <span className="line-chart-title">{title}</span>
        <span className="muted bump-meaning">{rankMeaning}</span>
      </div>

      <svg
        viewBox={`0 0 ${WIDTH} ${height}`}
        className="bump-svg"
        role="img"
        aria-label={`${title}. ${model.series.length} entries ranked over ${buckets.length} periods. ${rankMeaning}`}
      >
        {layout.rowYs.map((y, i) => (
          <g key={`row-${i}`}>
            <line className="bump-grid" x1={PAD.left} x2={WIDTH - PAD.right} y1={y} y2={y} />
            <text className="bump-axis" x={PAD.left - 10} y={y} textAnchor="end" dominantBaseline="middle">
              {i + 1}
            </text>
          </g>
        ))}
        {layout.xs.map((x, i) => (
          <text key={`x-${i}`} className="bump-axis" x={x} y={height - 8} textAnchor="middle">
            {bucketLabels[i]}
          </text>
        ))}

        {model.series.map((s, si) => {
          const lay = layout.series[si];
          const last = lay.points[lay.points.length - 1];
          return (
            <g key={s.id} className={`bump-series bump-s${s.slot}`} data-dim={dim(s.id) ? "true" : "false"} data-series={s.id}>
              {lay.paths.map((d, i) => (
                <path key={i} className="bump-line" d={d} />
              ))}
              {lay.points.map((p) => (
                <g key={p.bucket}>
                  <circle className="bump-dot" cx={p.x} cy={p.y} r={5} />
                  <circle
                    className="bump-hit"
                    cx={p.x}
                    cy={p.y}
                    r={13}
                    onPointerEnter={() => {
                      setActive(s.id);
                      setPoint({ id: s.id, bucket: p.bucket });
                    }}
                    onPointerLeave={() => {
                      setActive(null);
                      setPoint(null);
                    }}
                  />
                </g>
              ))}
              {last && (
                <text className="bump-end-label" x={last.x + 12} y={last.y} dominantBaseline="middle">
                  {s.label.length > 20 ? `${s.label.slice(0, 19)}…` : s.label}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      <div className="bump-readout" aria-live="polite">
        {shown && point && shownValue != null && shownRank != null
          ? `${shown.label} — rank ${shownRank} · ${formatValue(shownValue, unit, digits)} · ${bucketLabels[point.bucket]}`
          : "Hover a dot for its value."}
      </div>

      <div className="bump-legend" role="group" aria-label={`${title} legend`}>
        {model.series.map((s) => (
          <button
            key={s.id}
            className={`bump-legend-item bump-s${s.slot}`}
            aria-pressed={pinned === s.id}
            onPointerEnter={() => setActive(s.id)}
            onPointerLeave={() => setActive(null)}
            onFocus={() => setActive(s.id)}
            onBlur={() => setActive(null)}
            onClick={() => setPinned((cur) => (cur === s.id ? null : s.id))}
          >
            <span className="bump-swatch" aria-hidden="true" />
            {s.label}
          </button>
        ))}
      </div>
      {model.hidden > 0 && (
        <p className="muted bump-note">
          Showing {model.series.length} of {model.total} — the ones with the most data and the best average rank.
        </p>
      )}

      <details className="bump-table">
        <summary>Show the numbers</summary>
        <table>
          <thead>
            <tr>
              <th scope="col">Name</th>
              {bucketLabels.map((l, i) => (
                <th key={i} scope="col">
                  {l}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {model.series.map((s) => (
              <tr key={s.id}>
                <th scope="row">{s.label}</th>
                {s.values.map((v, i) => (
                  <td key={i}>{v == null ? "—" : `#${s.ranks[i]} · ${formatValue(v, unit, digits)}`}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
