import type { CurrentMapping, RuntimeSummary } from "../api";

interface MappingPanelProps {
  mapping: CurrentMapping;
  runtimes: RuntimeSummary[];
}

/**
 * The direct answer to "which agents are using which LLM right now" --
 * e.g. LLM1 -> Agent2, Agent3. Only runtimes with at least one active
 * agent link are shown; an idle LLM just doesn't appear here, since an
 * empty "not in use" row for every idle runtime would bury the signal.
 */
export function MappingPanel({ mapping, runtimes }: MappingPanelProps) {
  const runtimeName = (id: number) => runtimes.find((r) => r.id === id)?.name ?? `runtime #${id}`;
  const entries = Object.entries(mapping).filter(([, agents]) => agents.length > 0);

  if (entries.length === 0) {
    return (
      <div className="panel empty-state">No agent is currently attached to any LLM.</div>
    );
  }

  return (
    <div className="panel mapping-list">
      {entries.map(([runtimeId, agents]) => (
        <div className="mapping-row" key={runtimeId}>
          <span className="mono">{runtimeName(Number(runtimeId))}</span>
          <span className="mapping-arrow">→</span>
          {agents.map((a) => (
            <span className="agent-chip" key={a.agent_id}>
              {a.agent_name ?? `agent #${a.agent_id}`} ({a.requests_count})
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}
