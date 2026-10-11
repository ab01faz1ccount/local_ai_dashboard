import type { ModelComparisonRow } from "../api";
import { factKeysInOrder, findFact, safeSourceUrl } from "../knowledge-utils";
import { FactCell } from "./FactCell";
import { FitBadge } from "./FitBadge";

function formatMb(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(0)} MB`;
}

/**
 * Renders the four data sources the comparison feature combines as
 * labeled row-groups rather than flattening them into one undifferentiated
 * table -- estimated figures and observed figures should never look like
 * the same kind of number to the user.
 */
export function ModelCompareTable({
  rows,
  internetAvailable,
  onLinkRepo,
}: {
  rows: ModelComparisonRow[];
  internetAvailable: boolean;
  /** Called when the user accepts the repository the app found by searching a model's name. */
  onLinkRepo?: (modelId: number, repo: string) => void;
}) {
  if (rows.length === 0) return null;
  const webRows = factKeysInOrder(rows.map((r) => r.knowledge?.profile));

  return (
    <div className="compare-table-wrap">
      <table className="compare-table">
        <thead>
          <tr>
            <th>Model</th>
            {rows.map((r) => (
              <th key={r.metadata.id}>{r.metadata.name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr className="compare-section-row">
            <td colSpan={rows.length + 1}>Metadata</td>
          </tr>
          <tr>
            <td>Size</td>
            {rows.map((r) => (
              <td key={r.metadata.id}>{formatMb(r.metadata.file_size_bytes / (1024 * 1024))}</td>
            ))}
          </tr>
          <tr>
            <td>Quantization</td>
            {rows.map((r) => (
              <td key={r.metadata.id}>{r.metadata.quantization ?? "—"}</td>
            ))}
          </tr>
          <tr>
            <td>Params</td>
            {rows.map((r) => (
              <td key={r.metadata.id}>{r.metadata.param_count ?? "—"}</td>
            ))}
          </tr>
          <tr>
            <td>Context length</td>
            {rows.map((r) => (
              <td key={r.metadata.id}>{r.metadata.context_length ?? "—"}</td>
            ))}
          </tr>

          <tr className="compare-section-row">
            <td colSpan={rows.length + 1}>Estimated system impact</td>
          </tr>
          <tr>
            <td>Est. total RAM/VRAM</td>
            {rows.map((r) => (
              <td key={r.metadata.id}>{formatMb(r.estimated_system_impact.estimated_total_mb)}</td>
            ))}
          </tr>
          <tr>
            <td>Fit on this machine</td>
            {rows.map((r) => (
              <td key={r.metadata.id}>
                <FitBadge fit={r.hardware_fit} />
              </td>
            ))}
          </tr>

          <tr className="compare-section-row">
            <td colSpan={rows.length + 1}>Real-world performance (observed by this app)</td>
          </tr>
          <tr>
            <td>Requests logged</td>
            {rows.map((r) => (
              <td key={r.metadata.id}>{r.real_world_performance.requests_count}</td>
            ))}
          </tr>
          <tr>
            <td>Avg tokens/sec</td>
            {rows.map((r) => (
              <td key={r.metadata.id}>{r.real_world_performance.avg_tokens_per_sec?.toFixed(1) ?? "no data yet"}</td>
            ))}
          </tr>
          <tr>
            <td>Avg latency</td>
            {rows.map((r) => (
              <td key={r.metadata.id}>
                {r.real_world_performance.avg_latency_ms != null
                  ? `${r.real_world_performance.avg_latency_ms.toFixed(0)} ms`
                  : "no data yet"}
              </td>
            ))}
          </tr>

          <tr className="compare-section-row">
            <td colSpan={rows.length + 1}>
              From the web — every fact links to its source{!internetAvailable && " (needs an internet connection)"}
            </td>
          </tr>
          <tr>
            <td>Looked up as</td>
            {rows.map((r) => {
              const k = r.knowledge;
              const link = safeSourceUrl(k?.profile?.url);
              return (
                <td key={r.metadata.id}>
                  {!k || k.status === "offline" ? (
                    <span className="fact-missing">Offline — can't look this up</span>
                  ) : k.status === "none" ? (
                    <span className="fact-missing">No matching Hugging Face repo found — link one from the Models table</span>
                  ) : k.status === "error" ? (
                    <span className="error-text">{k.error ?? "Lookup failed."}</span>
                  ) : (
                    <div className="fact-cell">
                      {link ? (
                        <a className="fact-source" href={link} target="_blank" rel="noreferrer noopener">
                          {k.repo} ↗
                        </a>
                      ) : (
                        <span>{k.repo}</span>
                      )}
                      {k.status === "suggested" && (
                        <div className="fact-meta">
                          <span className="fact-tag" data-provenance="derived">
                            best match from searching its name — check it is the right model
                          </span>
                          {onLinkRepo && k.repo && (
                            <button className="link-button" onClick={() => onLinkRepo(r.metadata.id, k.repo!)}>
                              Link it
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </td>
              );
            })}
          </tr>
          {webRows.map(({ key, label }) => (
            <tr key={key}>
              <td>{label}</td>
              {rows.map((r) => (
                <td key={r.metadata.id}>
                  {r.knowledge?.profile ? <FactCell fact={findFact(r.knowledge.profile, key)} /> : <span className="fact-missing">—</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
