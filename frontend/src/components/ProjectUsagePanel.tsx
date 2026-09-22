import { useEffect, useState } from "react";
import { api, type ProjectHardwareUsage } from "../api";

/**
 * Shows what share of each runtime's hardware load this project is
 * estimated to account for. Exact when a runtime was only ever used by
 * this project (`exclusive_to_project`); apportioned by request-count
 * share otherwise -- both cases render with the same shape, but shared
 * runtimes get a visible "~" and the note, so the number never reads as
 * more precise than it is.
 */
export function ProjectUsagePanel({ projectId }: { projectId: string }) {
  const [usage, setUsage] = useState<ProjectHardwareUsage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setUsage(null);
    setError(null);
    api
      .getProjectHardwareUsage(projectId)
      .then(setUsage)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load usage."));
  }, [projectId]);

  if (error) return <div className="error-text">{error}</div>;
  if (!usage) return <p className="muted">در حال بارگذاری...</p>;

  if (usage.runtimes.length === 0) {
    return <p className="muted">این پروژه هنوز هیچ runtime‌ای استفاده نکرده.</p>;
  }

  return (
    <div className="git-panel">
      <div className="panel git-diff-summary">
        <div className="section-heading">
          <h3>خلاصه</h3>
        </div>
        <p className="mono">
          {usage.total_estimated_cpu_percent}% CPU (تخمینی) · {usage.total_estimated_ram_used_mb} MB RAM (تخمینی)
        </p>
        <p className="muted">
          {usage.exclusive_runtime_count} runtime اختصاصی · {usage.shared_runtime_count} runtime مشترک
        </p>
      </div>

      {usage.runtimes.map((rt) => (
        <div className="panel git-diff-summary" key={rt.runtime_id}>
          <div className="section-heading">
            <h3>Runtime #{rt.runtime_id}</h3>
            <span className="muted">
              {rt.exclusive_to_project ? "اختصاصی" : `${((rt.request_share ?? 0) * 100).toFixed(0)}% از درخواست‌ها`}
            </span>
          </div>
          <p className="mono">
            {rt.exclusive_to_project ? "" : "~"}
            {rt.estimated_project_metrics.estimated_cpu_percent ?? "—"}% CPU ·{" "}
            {rt.estimated_project_metrics.estimated_ram_used_mb ?? "—"} MB RAM
            {rt.runtime_avg_metrics.avg_gpu_percent != null && (
              <>
                {" "}
                · {rt.exclusive_to_project ? "" : "~"}
                {rt.estimated_project_metrics.estimated_gpu_percent}% GPU
              </>
            )}
          </p>
          <p className="muted">
            {rt.project_requests} / {rt.total_requests_on_runtime} درخواست این runtime مال این پروژه بوده
          </p>
        </div>
      ))}

      <p className="muted" style={{ fontSize: 12 }}>
        {usage.note}
      </p>
    </div>
  );
}
