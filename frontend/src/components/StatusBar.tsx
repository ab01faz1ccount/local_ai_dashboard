import { useState } from "react";
import { ACTIVITY_LABEL, countByStatus, type ActivityBucket, type ActivityStatus } from "../status-bar-utils";

const ORDER: ActivityStatus[] = ["active", "idle", "none"];

const clock = (ms: number) => new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });

/** A strip of equal time slots, each coloured by what the runtime was doing.
 * Colour is never the only channel: the legend counts every state, hovering
 * a slot names it, and a hidden table lists them all for screen readers. */
export function StatusBar({ buckets, label }: { buckets: ActivityBucket[]; label: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const counts = countByStatus(buckets);
  const active = hover != null ? buckets[hover] : null;

  return (
    <section className="status-bar panel" aria-label={label}>
      <div className="status-bar-head">
        <span className="status-bar-title">{label}</span>
        <span className="status-bar-readout muted" aria-live="polite">
          {active
            ? `${clock(active.start)}–${clock(active.end)} · ${ACTIVITY_LABEL[active.status]}${
                active.samples > 0 ? ` · ${active.samples} samples` : ""
              }`
            : buckets.length > 0
              ? `${clock(buckets[0].start)}–${clock(buckets[buckets.length - 1].end)}`
              : ""}
        </span>
      </div>
      <div className="status-bar-track" onMouseLeave={() => setHover(null)} data-testid="status-bar-track">
        {buckets.map((b, i) => (
          <span
            key={b.start}
            className="status-bar-cell"
            data-status={b.status}
            data-hover={hover === i ? "true" : undefined}
            onMouseEnter={() => setHover(i)}
          />
        ))}
      </div>
      <div className="status-bar-legend">
        {ORDER.map((s) => (
          <span key={s} className="status-bar-key">
            <span className="status-bar-swatch" data-status={s} />
            {ACTIVITY_LABEL[s]} <span className="muted mono">{counts[s]}</span>
          </span>
        ))}
      </div>
      <div className="sr-only">
        <table>
          <caption>{label} data</caption>
          <thead>
            <tr>
              <th scope="col">From</th>
              <th scope="col">To</th>
              <th scope="col">Status</th>
              <th scope="col">Samples</th>
            </tr>
          </thead>
          <tbody>
            {buckets.map((b) => (
              <tr key={b.start}>
                <th scope="row">{clock(b.start)}</th>
                <td>{clock(b.end)}</td>
                <td>{ACTIVITY_LABEL[b.status]}</td>
                <td>{b.samples}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
