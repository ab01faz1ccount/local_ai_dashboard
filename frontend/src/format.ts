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
