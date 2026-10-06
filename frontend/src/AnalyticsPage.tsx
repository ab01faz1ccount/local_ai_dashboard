import { useCallback, useEffect, useMemo, useState } from "react";
import { api, apiErrorMessage, type MetricsSnapshotItem, type RuntimeSummary } from "./api";
import { buildSeries, rangeToSince, type TimeRange } from "./analytics-utils";
import { LineChart } from "./components/LineChart";

const RANGES: TimeRange[] = ["15m", "1h", "6h", "24h"];
const REFRESH_MS = 15_000;

/**
 * Analytics (master build prompt section 23): charts over the history
 * core/metrics.py's background sampler writes -- tokens/sec and latency
 * for the selected runtime, plus the machine's CPU/GPU/RAM/VRAM at each
 * sample. Refreshes on its own so a long-open tab keeps moving.
 */
export function AnalyticsPage() {
  const [runtimes, setRuntimes] = useState<RuntimeSummary[]>([]);
  const [runtimeId, setRuntimeId] = useState<number | null>(null);
  const [range, setRange] = useState<TimeRange>("1h");
  const [snapshots, setSnapshots] = useState<MetricsSnapshotItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listRuntimes()
      .then((list) => {
        setRuntimes(list);
        setRuntimeId((cur) => cur ?? list[0]?.id ?? null);
        if (list.length === 0) setLoading(false);
      })
      .catch((err) => {
        setError(apiErrorMessage(err, "Could not load runtimes."));
        setLoading(false);
      });
  }, []);

  const load = useCallback(async () => {
    if (runtimeId == null) return;
    try {
      setSnapshots(await api.getRuntimeMetricsHistory(runtimeId, { since: rangeToSince(range), limit: 2000 }));
      setError(null);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not load metrics history."));
    } finally {
      setLoading(false);
    }
  }, [runtimeId, range]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const series = useMemo(
    () => ({
      tps: buildSeries(snapshots, "tokens_per_sec"),
      latency: buildSeries(snapshots, "last_latency_ms"),
      cpu: buildSeries(snapshots, "cpu_percent"),
      ram: buildSeries(snapshots, "ram_percent"),
      gpu: buildSeries(snapshots, "gpu_percent"),
      vram: buildSeries(snapshots, "vram_percent"),
    }),
    [snapshots]
  );

  return (
    <div className="main">
      <div className="section-heading">
        <h2>Analytics</h2>
      </div>
      <p className="muted mcp-subtitle">
        History sampled by the server every few seconds while a runtime is online — it keeps recording even when no
        browser tab is open.
      </p>

      <div className="logs-toolbar">
        <select
          aria-label="Runtime"
          value={runtimeId ?? ""}
          onChange={(e) => {
            setLoading(true);
            setRuntimeId(Number(e.target.value));
          }}
        >
          {runtimes.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
        <div className="analytics-ranges" role="group" aria-label="Time range">
          {RANGES.map((r) => (
            <button key={r} className={r === range ? "primary" : ""} aria-pressed={r === range} onClick={() => setRange(r)}>
              {r}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {runtimes.length === 0 && !loading ? (
        <div className="panel empty-state">No runtimes yet — add one from the dashboard to start collecting history.</div>
      ) : loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <div className="analytics-grid">
          <LineChart title="Tokens / sec" points={series.tps} unit=" t/s" />
          <LineChart title="Latency" points={series.latency} unit=" ms" digits={0} />
          <LineChart title="CPU" points={series.cpu} unit="%" fixedMax={100} />
          <LineChart title="RAM" points={series.ram} unit="%" fixedMax={100} />
          <LineChart title="GPU" points={series.gpu} unit="%" fixedMax={100} />
          <LineChart title="VRAM" points={series.vram} unit="%" fixedMax={100} />
        </div>
      )}
    </div>
  );
}
