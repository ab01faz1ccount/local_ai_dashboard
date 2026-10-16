import type { ModelComparisonRow } from "../api";
import { bitsText, formatContext, headsText, kvCacheText, sharedWebRepo, tensorMixText, valuesDiffer } from "../compare-utils";
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
  const sharedRepo = sharedWebRepo(rows);
  const anyFile = rows.some((r) => r.gguf);

  /** One comparison row. Rows whose shown values differ between the models are highlighted,
   * so what actually distinguishes the files stands out from what is merely the same. */
  const line = (label: string, cells: string[], title?: string) => (
    <tr className={valuesDiffer(cells) ? "compare-differs" : undefined} title={title}>
      <td>{label}</td>
      {rows.map((r, i) => (
        <td key={r.metadata.id}>{cells[i]}</td>
      ))}
    </tr>
  );

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
          {line("Quantization", rows.map((r) => r.metadata.quantization ?? "—"), "Read from the file when possible")}
          {line("Params", rows.map((r) => r.metadata.param_count ?? "—"), "Read from the file when possible, otherwise guessed from the name")}
          {line("Context length", rows.map((r) => (r.metadata.context_length != null ? formatContext(r.metadata.context_length) : "—")), "The most the model was trained for")}

          <tr className="compare-section-row">
            <td colSpan={rows.length + 1}>
              Read from the file itself — exact for each file
              {!anyFile && " (the files could not be read)"}
            </td>
          </tr>
          {anyFile ? (
            <>
              {line("Architecture", rows.map((r) => r.gguf?.architecture ?? "—"))}
              {line("Parameters (exact)", rows.map((r) => r.gguf?.parameters_label ?? "—"), "Counted from the tensors in the file")}
              {line("Weight types", rows.map((r) => tensorMixText(r.gguf?.tensor_mix)), "Which quantization types the weights really use. A Q4_K_M file is mostly Q4_K with some Q6_K")}
              {line("Bits per weight", rows.map((r) => bitsText(r.gguf?.bits_per_weight)), "File size divided by parameters: the real cost of the quantization")}
              {line("Layers", rows.map((r) => (r.gguf?.layers != null ? String(r.gguf.layers) : "—")))}
              {line("Embedding size", rows.map((r) => (r.gguf?.embedding_length != null ? String(r.gguf.embedding_length) : "—")))}
              {line("Attention heads", rows.map((r) => headsText(r.gguf)))}
              {line("Vocabulary", rows.map((r) => (r.gguf?.vocab_size != null ? r.gguf.vocab_size.toLocaleString("en-US") : "—")))}
              {line("Chat template", rows.map((r) => (r.gguf ? (r.gguf.has_chat_template ? "built in" : "none") : "—")))}
              {line(
                "KV cache per 1k tokens",
                rows.map((r) => kvCacheText(r.gguf?.kv_cache, 1000)),
                "Estimate at f16, from the layer and head counts. Models with mixed layer kinds only cache some layers."
              )}
              {rows.some((r) => r.gguf_error) && (
                <tr>
                  <td>Could not read</td>
                  {rows.map((r) => (
                    <td key={r.metadata.id}>{r.gguf_error ? <span className="fact-missing">{r.gguf_error}</span> : ""}</td>
                  ))}
                </tr>
              )}
            </>
          ) : (
            <tr>
              <td>Why</td>
              <td colSpan={rows.length}>
                <span className="fact-missing">{rows.find((r) => r.gguf_error)?.gguf_error ?? "No file details are available."}</span>
              </td>
            </tr>
          )}

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
          {sharedRepo && (
            <tr className="compare-note-row">
              <td colSpan={rows.length + 1}>
                These models come from the same repository ({sharedRepo}), so the web facts below are identical by nature: a model card describes the whole
                repository, not one quantization. What tells these files apart is in the rows above.
              </td>
            </tr>
          )}
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
