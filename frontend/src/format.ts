/** Small formatting helpers shared across the dashboard and wizard. */

export function formatMb(mb: number | null | undefined): string {
  if (mb == null) return "—";
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${Math.round(mb)} MB`;
}

export function formatBytes(bytes: number): string {
  return formatMb(bytes / (1024 * 1024));
}

export function formatPercent(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${value.toFixed(0)}%`;
}

export function formatTokensPerSec(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${value.toFixed(1)} tok/s`;
}

/** HH:MM:SS in the viewer's own locale/timezone -- used for the Logs
 * page's per-row timestamp, where the full date is redundant almost all
 * the time (see formatFullTimestamp for the hover/detail form). */
export function formatClockTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString(undefined, { hour12: false });
}

/** Full date + time, for a title/tooltip -- the ISO string itself is UTC
 * and not what a person reads at a glance. */
export function formatFullTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { hour12: false });
}
