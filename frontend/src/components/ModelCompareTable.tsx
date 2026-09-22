import type { ModelComparisonRow } from "../api";

function formatMb(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(0)} MB`;
}

/**
 * Renders the four data sources the comparison feature combines as
 * labeled row-groups rather than flattening them into one undifferentiated
 * table -- estimated figures and observed figures should never look like
 * the same kind of number to the user.
 */
export function ModelCompareTable({ rows, internetAvailable }: { rows: ModelComparisonRow[]; internetAvailable: boolean }) {
  if (rows.length === 0) return null;

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
              Public benchmarks (Hugging Face card){!internetAvailable && " — نیاز به اتصال اینترنت"}
            </td>
          </tr>
          <tr>
            <td>Eval results</td>
            {rows.map((r) => (
              <td key={r.metadata.id}>
                {!r.metadata.hf_repo_id ? (
                  "not linked to HF"
                ) : !internetAvailable ? (
                  "آفلاین — نمی‌شه چک کرد"
                ) : !r.public_benchmarks || r.public_benchmarks.eval_results.length === 0 ? (
                  "none published"
                ) : (
                  <ul className="compare-eval-list">
                    {r.public_benchmarks.eval_results.map((ev, i) => (
                      <li key={i}>
                        {ev.dataset ?? ev.task ?? "eval"}: {ev.metric_name} = {ev.value}
                      </li>
                    ))}
                  </ul>
                )}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}
