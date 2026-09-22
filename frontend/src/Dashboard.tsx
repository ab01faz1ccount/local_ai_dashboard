import { useEffect, useState } from "react";
import {
  api,
  connectMetricsSocket,
  type CurrentMapping,
  type HardwareSnapshot,
  type ModelSummary,
  type RuntimeMetrics,
  type RuntimeSummary,
} from "./api";
import { HardwareStrip } from "./components/HardwareStrip";
import { MappingPanel } from "./components/MappingPanel";
import { RuntimeCard } from "./components/RuntimeCard";

/**
 * The single-page dashboard: always-visible hardware strip, a grid of
 * runtime cards (status/model/metrics/controls), and the live
 * LLM<->Agent mapping. Runtime list + per-runtime metrics come from REST
 * on load and after every action; the hardware strip and the "is it still
 * ONLINE" ticks come from the WebSocket push so they stay live without
 * polling.
 *
 * `onAddRuntime` is called both by the "+ Add runtime" card and by the
 * empty-state prompt shown when there's nothing configured yet -- the
 * parent shell decides what that means (today: switch to the Setup nav
 * item, which is the old onboarding wizard reused as a normal page).
 */
export function Dashboard({ onAddRuntime }: { onAddRuntime: () => void }) {
  const [runtimes, setRuntimes] = useState<RuntimeSummary[]>([]);
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [metricsByRuntime, setMetricsByRuntime] = useState<Record<number, RuntimeMetrics>>({});
  const [mapping, setMapping] = useState<CurrentMapping>({});
  const [hardware, setHardware] = useState<HardwareSnapshot | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  async function refreshRuntimes() {
    const list = await api.listRuntimes();
    setRuntimes(list);
    const entries = await Promise.all(
      list.map(async (rt) => [rt.id, await api.getRuntimeMetrics(rt.id)] as const)
    );
    setMetricsByRuntime(Object.fromEntries(entries));
  }

  async function refreshMapping() {
    setMapping(await api.getCurrentMapping());
  }

  useEffect(() => {
    (async () => {
      try {
        await Promise.all([refreshRuntimes(), refreshMapping()]);
        setModels(await api.listModels());
      } catch (err) {
        setLoadError(err instanceof Error ? err.message : "Failed to load dashboard data.");
      }
    })();
  }, []);

  useEffect(() => {
    const close = connectMetricsSocket((snapshot) => {
      setHardware(snapshot);
      // Keep the visible status/tokens-per-sec in sync between the
      // slower REST refreshes triggered by user actions, without a
      // second polling loop.
      setRuntimes((prev) =>
        prev.map((rt) => {
          const live = snapshot.runtimes.find((r) => r.runtime_id === rt.id);
          return live ? { ...rt, status: live.state } : rt;
        })
      );
    });
    return close;
  }, []);

  async function handleStart(id: number) {
    await api.startRuntime(id);
    await refreshRuntimes();
  }

  async function handleStop(id: number) {
    await api.stopRuntime(id);
    await refreshRuntimes();
  }

  async function handleRestart(id: number) {
    await api.restartRuntime(id);
    await refreshRuntimes();
  }

  async function handleDelete(id: number) {
    try {
      await api.deleteRuntime(id);
      await refreshRuntimes();
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Could not delete this runtime.");
    }
  }

  async function handleEdit(
    id: number,
    body: { name?: string; host?: string; port?: number; model_id?: number; executable_path?: string }
  ) {
    await api.updateRuntimeConfig(id, body);
    await refreshRuntimes();
  }

  return (
    <div className="main">
      <HardwareStrip snapshot={hardware} />

      {loadError && <div className="error-text">{loadError}</div>}

      {runtimes.length === 0 && !loadError && (
        <div className="panel empty-state">
          <h2 style={{ marginTop: 0 }}>Nothing set up yet</h2>
          <p className="muted">
            Detect llama.cpp, pick a model, and get your first runtime online.
          </p>
          <button className="primary" onClick={onAddRuntime}>
            Set up your first LLM
          </button>
        </div>
      )}

      <section>
        <div className="section-heading">
          <h2>Runtimes</h2>
        </div>
        <div className="runtime-grid">
          {runtimes.map((rt) => (
            <RuntimeCard
              key={rt.id}
              runtime={rt}
              metrics={metricsByRuntime[rt.id]}
              models={models}
              onStart={handleStart}
              onStop={handleStop}
              onRestart={handleRestart}
              onDelete={handleDelete}
              onEdit={handleEdit}
            />
          ))}
          <button className="add-runtime-card" onClick={onAddRuntime}>
            + Add runtime
          </button>
        </div>
      </section>

      <section>
        <div className="section-heading">
          <h2>Who's using what</h2>
        </div>
        <MappingPanel mapping={mapping} runtimes={runtimes} />
      </section>
    </div>
  );
}
