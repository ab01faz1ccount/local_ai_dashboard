/**
 * frontend/src/status-bar-utils.ts
 *
 * The activity strip on the Analytics page: the chosen time range cut into
 * equal slots, each saying what the runtime was doing then. Pure.
 */

import type { MetricsSnapshotItem } from "./api";

export type ActivityStatus = "active" | "idle" | "none";

export interface ActivityBucket {
  /** ms since epoch, [start, end) */
  start: number;
  end: number;
  status: ActivityStatus;
  samples: number;
}

export const ACTIVITY_LABEL: Record<ActivityStatus, string> = {
  active: "Generating",
  idle: "Online, idle",
  none: "No data",
};

/** `active` when a sample in the slot measured generation speed above zero,
 * `idle` when the runtime was sampled but never generating, `none` when
 * nothing was sampled (the runtime was off, or the app was closed). */
export function bucketActivity(
  snapshots: Pick<MetricsSnapshotItem, "timestamp" | "tokens_per_sec">[],
  rangeMs: number,
  now: number,
  slots = 48
): ActivityBucket[] {
  const width = rangeMs / slots;
  const from = now - rangeMs;
  const buckets: ActivityBucket[] = Array.from({ length: slots }, (_, i) => ({
    start: from + i * width,
    end: from + (i + 1) * width,
    status: "none" as ActivityStatus,
    samples: 0,
  }));
  for (const s of snapshots) {
    const t = Date.parse(s.timestamp);
    if (!Number.isFinite(t) || t < from || t > now) continue;
    const i = Math.min(slots - 1, Math.floor((t - from) / width));
    const b = buckets[i];
    b.samples += 1;
    const generating = s.tokens_per_sec != null && s.tokens_per_sec > 0;
    if (generating) b.status = "active";
    else if (b.status === "none") b.status = "idle";
  }
  return buckets;
}

export function countByStatus(buckets: ActivityBucket[]): Record<ActivityStatus, number> {
  const out: Record<ActivityStatus, number> = { active: 0, idle: 0, none: 0 };
  for (const b of buckets) out[b.status] += 1;
  return out;
}
