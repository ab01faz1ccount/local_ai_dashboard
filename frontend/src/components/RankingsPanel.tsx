import { useCallback, useEffect, useMemo, useState } from "react";
import { api, apiErrorMessage, type AgentSummary, type MetricsSnapshotItem, type RuntimeSummary, type UsageHistoryEntry } from "../api";
import {
  AGENT_METRICS,
  RANK_RANGES,
  RUNTIME_METRICS,
  agentEntries,
  bucketLabel,
  buildBump,
  makeBuckets,
  runtimeEntries,
  type RankSubject,
} from "../bump-utils";
import { BumpChart } from "./BumpChart";

const REFRESH_MS = 30_000;
const MAX_RUNTIMES = 12;

/**
 * Who is ahead, and when did that change? Ranks the LLM runtimes by measured
 * speed or latency, or the agents by what they actually did, bucket by bucket.
 * Everything comes from what was recorded -- nothing is estimated.
 */
export function RankingsPanel() {
  const [subject, setSubject] = useState<RankSubject>("llm");
  const [rangeKey, setRangeKey] = useState<string>("6h");
  const [metricKey, setMetricKey] = useState<string>("tokens_per_sec");
  const [runtimes, setRuntimes] = useState<RuntimeSummary[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [snapshots, setSnapshots] = useState<Record<number, MetricsSnapshotItem[]>>({});
  const [sessions, setSessions] = useState<UsageHistoryEntry[]>([]);
  const [period, setPeriod] = useState<{ start: number; end: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const ranges = RANK_RANGES[subject];
  const range = ranges.find((r) => r.key === rangeKey) ?? ranges[0];
  const metrics = subject === "llm" ? RUNTIME_METRICS : AGENT_METRICS;
  const metric = metrics.find((m) => m.key === metricKey) ?? metrics[0];

  function pickSubject(next: RankSubject) {
    setSubject(next);
    setRangeKey(RANK_RANGES[next][0].key);
    setMetricKey(next === "llm" ? RUNTIME_METRICS[0].key : AGENT_METRICS[0].key);
    setLoading(true);
  }

  const load = useCallback(async () => {
    const end = Date.now();
    const start = end - range.ms;
    try {
      if (subject === "llm") {
        const list = (await api.listRuntimes()).slice(0, MAX_RUNTIMES);
        const since = new Date(start).toISOString();
        const entries = await Promise.all(
          list.map(async (r) => [r.id, await api.getRuntimeMetricsHistory(r.id, { since, limit: 5000 })] as const)
        );
        setRuntimes(list);
        setSnapshots(Object.fromEntries(entries));
      } else {
        const [agentList, history] = await Promise.all([api.listAgents(), api.getUsageHistory({ limit: 1000 })]);
        setAgents(agentList);
        setSessions(history);
      }
      setPeriod({ start, end });
      setError(null);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not load the data to rank."));
    } finally {
      setLoading(false);
    }
  }, [subject, range.ms]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const view = useMemo(() => {
    if (!period) return null;
    const buckets = makeBuckets(period.start, period.end, range.buckets);
    const entries =
      subject === "llm"
        ? runtimeEntries(runtimes, snapshots, metric.key as never, buckets)
        : agentEntries(agents, sessions, metric.key as never, buckets);
    return {
      buckets,
      labels: buckets.map((b) => bucketLabel(b, range.ms)),
      model: buildBump(entries, metric.direction),
      count: subject === "llm" ? runtimes.length : agents.length,
    };
  }, [period, range, subject, runtimes, snapshots, agents, sessions, metric]);

  return (
    <section className="rankings">
      <div className="section-heading">
        <h2>Rankings over time</h2>
      </div>
      <div className="logs-toolbar">
        <div className="segmented" role="group" aria-label="Rank">
          {(["llm", "agent"] as const).map((s) => (
            <button key={s} className={subject === s ? "selected" : ""} aria-pressed={subject === s} onClick={() => pickSubject(s)}>
              {s === "llm" ? "LLMs" : "Agents"}
            </button>
          ))}
        </div>
        <select aria-label="Rank by" value={metric.key} onChange={(e) => setMetricKey(e.target.value)}>
          {metrics.map((m) => (
            <option key={m.key} value={m.key}>
              {m.label}
            </option>
          ))}
        </select>
        <div className="analytics-ranges" role="group" aria-label="Ranking period">
          {ranges.map((r) => (
            <button key={r.key} className={r.key === range.key ? "primary" : ""} aria-pressed={r.key === range.key} onClick={() => setRangeKey(r.key)}>
              {r.label}
            </button>
          ))}
        </div>
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {loading || !view ? (
        <p className="muted">Loading…</p>
      ) : view.count === 0 ? (
        <div className="panel empty-state">{subject === "llm" ? "No runtimes yet." : "No agents yet."}</div>
      ) : (
        <BumpChart
          title={`${subject === "llm" ? "LLMs" : "Agents"} by ${metric.label.toLowerCase()}`}
          model={view.model}
          buckets={view.buckets}
          bucketLabels={view.labels}
          unit={metric.unit}
          digits={metric.digits}
          rankMeaning={metric.direction === "higher" ? "1 = highest" : "1 = lowest"}
        />
      )}
    </section>
  );
}
