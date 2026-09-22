import { useEffect, useRef, useState } from "react";
import { api, type ModelComparisonRow, type ModelSummary } from "./api";
import { ModelCompareTable } from "./components/ModelCompareTable";
import { VerificationBadge } from "./components/VerificationBadge";

/**
 * Model comparison + authenticity verification, in one page since both
 * features operate on the same model list and the user picks models for
 * each from the same table.
 *
 * Verification is opt-in per model (per the roadmap requirement): linking
 * a model to a Hugging Face repo/file does nothing by itself -- the user
 * has to press "Verify" to actually trigger the hash/lookup work, which
 * runs as a backend background task and is polled here until it leaves
 * "pending".
 */
export function ModelsPage() {
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [comparison, setComparison] = useState<ModelComparisonRow[] | null>(null);
  const [comparisonOnline, setComparisonOnline] = useState(true);
  const [comparing, setComparing] = useState(false);
  const [online, setOnline] = useState(true);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [hfRepoDraft, setHfRepoDraft] = useState("");
  const [hfFileDraft, setHfFileDraft] = useState("");
  const pollHandles = useRef<Record<number, number>>({});

  async function refreshModels() {
    try {
      setModels(await api.listModels());
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load models.");
    }
  }

  useEffect(() => {
    void refreshModels();
    api
      .getConnectivity()
      .then((r) => setOnline(r.online))
      .catch(() => setOnline(true)); // don't block the UI on a failed check itself
    return () => {
      Object.values(pollHandles.current).forEach((h) => window.clearInterval(h));
    };
  }, []);

  function toggleSelected(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleCompare() {
    if (selectedIds.size < 2) return;
    setComparing(true);
    try {
      const { internet_available, rows } = await api.compareModels(Array.from(selectedIds));
      setComparison(rows);
      setComparisonOnline(internet_available);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Comparison failed.");
    } finally {
      setComparing(false);
    }
  }

  function startEditingLink(model: ModelSummary) {
    setEditingId(model.id);
    setHfRepoDraft(model.hf_repo_id ?? "");
    setHfFileDraft(model.hf_filename ?? "");
  }

  async function saveLink(id: number) {
    const updated = await api.updateModel(id, {
      hf_repo_id: hfRepoDraft.trim(),
      hf_filename: hfFileDraft.trim(),
    });
    setModels((prev) => prev.map((m) => (m.id === id ? updated : m)));
    setEditingId(null);
  }

  function pollVerification(id: number) {
    if (pollHandles.current[id]) window.clearInterval(pollHandles.current[id]);
    const handle = window.setInterval(async () => {
      const result = await api.getModelVerification(id);
      if (result.verification_status !== "pending") {
        window.clearInterval(handle);
        delete pollHandles.current[id];
        setModels((prev) =>
          prev.map((m) =>
            m.id === id
              ? {
                  ...m,
                  verification_status: result.verification_status,
                  verification_checked_at: result.verification_checked_at,
                  verification_details: result.verification_details,
                }
              : m
          )
        );
      }
    }, 2000);
    pollHandles.current[id] = handle;
  }

  async function handleVerify(id: number) {
    try {
      const result = await api.startModelVerification(id);
      setModels((prev) =>
        prev.map((m) => (m.id === id ? { ...m, verification_status: result.verification_status } : m))
      );
      pollVerification(id);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Verification failed to start.");
      setOnline(false); // the backend re-checks with force=true, so a rejection here means we really are offline
    }
  }

  async function handleDelete(id: number, name: string) {
    if (!window.confirm(`«${name}» از فهرست مدل‌ها حذف بشه؟ (خود فایل روی دیسک پاک نمی‌شه، فقط ورودی حذف می‌شه.)`)) {
      return;
    }
    try {
      await api.deleteModel(id);
      setModels((prev) => prev.filter((m) => m.id !== id));
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Could not delete this model.");
    }
  }

  return (
    <div className="main">
      {loadError && <div className="error-text">{loadError}</div>}
      {!online && (
        <div className="panel offline-banner">
          بدون اتصال اینترنت — تایید اصالت مدل غیرفعاله، و بنچمارک‌های عمومی توی مقایسه در دسترس نیستن.
        </div>
      )}

      <section>
        <div className="section-heading">
          <h2>Models</h2>
          <button
            className="primary"
            disabled={selectedIds.size < 2 || comparing}
            onClick={() => void handleCompare()}
          >
            {comparing ? "Comparing…" : `Compare selected (${selectedIds.size})`}
          </button>
        </div>

        <table className="models-table">
          <thead>
            <tr>
              <th />
              <th>Name</th>
              <th>Size</th>
              <th>Quant</th>
              <th>Params</th>
              <th>Context</th>
              <th>Hugging Face source</th>
              <th>Authenticity</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.id}>
                <td>
                  <input
                    type="checkbox"
                    checked={selectedIds.has(m.id)}
                    onChange={() => toggleSelected(m.id)}
                  />
                </td>
                <td>{m.name}</td>
                <td>{(m.file_size_bytes / (1024 * 1024 * 1024)).toFixed(2)} GB</td>
                <td>{m.quantization ?? "—"}</td>
                <td>{m.param_count ?? "—"}</td>
                <td>{m.context_length ?? "—"}</td>
                <td>
                  {editingId === m.id ? (
                    <div className="hf-link-editor">
                      <input
                        placeholder="org/repo-name"
                        value={hfRepoDraft}
                        onChange={(e) => setHfRepoDraft(e.target.value)}
                      />
                      <input
                        placeholder="file-name.gguf"
                        value={hfFileDraft}
                        onChange={(e) => setHfFileDraft(e.target.value)}
                      />
                      <button onClick={() => void saveLink(m.id)}>Save</button>
                      <button onClick={() => setEditingId(null)}>Cancel</button>
                    </div>
                  ) : (
                    <button className="link-button" onClick={() => startEditingLink(m)}>
                      {m.hf_repo_id ? `${m.hf_repo_id}` : "Link to Hugging Face…"}
                    </button>
                  )}
                </td>
                <td>
                  <VerificationBadge status={m.verification_status} />
                  <button
                    className="verify-button"
                    disabled={!m.hf_repo_id || !m.hf_filename || m.verification_status === "pending" || !online}
                    title={!online ? "نیاز به اتصال اینترنت دارد" : undefined}
                    onClick={() => void handleVerify(m.id)}
                  >
                    Verify
                  </button>
                </td>
                <td>
                  <button className="verify-button" onClick={() => void handleDelete(m.id, m.name)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {comparison && (
        <section>
          <div className="section-heading">
            <h2>Comparison</h2>
            <button onClick={() => setComparison(null)}>Close</button>
          </div>
          <ModelCompareTable rows={comparison} internetAvailable={comparisonOnline} />
        </section>
      )}
    </div>
  );
}
