import { useEffect, useRef, useState } from "react";
import { api, type AgentBackendInfo, type AgentProfileResult, type AgentSummary, type ModelComparisonRow, type ModelSummary } from "./api";
import { AgentCompareTable } from "./components/AgentCompareTable";
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

  // "Agents" tab: what each agent is and can do, from its repository
  const [tab, setTab] = useState<"models" | "agents">("models");
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [backends, setBackends] = useState<AgentBackendInfo[]>([]);
  const [selectedAgentIds, setSelectedAgentIds] = useState<Set<number>>(new Set());
  const [agentProfiles, setAgentProfiles] = useState<Record<number, AgentProfileResult | undefined>>({});

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

  useEffect(() => {
    if (tab !== "agents" || agents.length > 0) return;
    Promise.all([api.listAgents(), api.listAgentBackends()])
      .then(([a, b]) => {
        setAgents(a);
        setBackends(b);
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Failed to load agents."));
  }, [tab, agents.length]);

  async function loadAgentProfile(id: number) {
    setAgentProfiles((prev) => ({ ...prev, [id]: undefined }));
    try {
      const result = await api.getAgentProfile(id);
      setAgentProfiles((prev) => ({ ...prev, [id]: result }));
    } catch (err) {
      setAgentProfiles((prev) => ({
        ...prev,
        [id]: { agent_id: id, status: "error", repo: null, repo_origin: null, profile: null, error: err instanceof Error ? err.message : "Lookup failed." },
      }));
    }
  }

  function toggleAgent(id: number) {
    const adding = !selectedAgentIds.has(id);
    setSelectedAgentIds((prev) => {
      const next = new Set(prev);
      if (adding) next.add(id);
      else next.delete(id);
      return next;
    });
    if (adding && agentProfiles[id] === undefined) void loadAgentProfile(id);
  }

  /** The app found a repo for this model by searching its name and the user accepted it. */
  async function handleLinkRepo(modelId: number, repo: string) {
    const updated = await api.updateModel(modelId, { hf_repo_id: repo });
    setModels((prev) => prev.map((m) => (m.id === modelId ? updated : m)));
    await handleCompare();
  }

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

      <div className="segmented" role="tablist" aria-label="What to compare">
        <button role="tab" aria-selected={tab === "models"} className={tab === "models" ? "selected" : ""} onClick={() => setTab("models")}>
          LLMs
        </button>
        <button role="tab" aria-selected={tab === "agents"} className={tab === "agents" ? "selected" : ""} onClick={() => setTab("agents")}>
          Agents
        </button>
      </div>

      {tab === "agents" && (
        <section>
          <div className="section-heading">
            <h2>Agents</h2>
          </div>
          <p className="muted">Pick one or more agents to see what each can do, from its own repository — every fact links to where it came from.</p>
          {agents.length === 0 ? (
            <div className="panel empty-state">No agents yet — add one from Setup.</div>
          ) : (
            <>
              <div className="agent-picker">
                {agents.map((a) => (
                  <label key={a.id} className="agent-pick">
                    <input type="checkbox" checked={selectedAgentIds.has(a.id)} onChange={() => toggleAgent(a.id)} />
                    {a.name}
                  </label>
                ))}
              </div>
              <AgentCompareTable
                agents={agents.filter((a) => selectedAgentIds.has(a.id))}
                backends={backends}
                profiles={agentProfiles}
                onLinked={(id) => void loadAgentProfile(id)}
              />
            </>
          )}
        </section>
      )}

      {tab === "models" && (
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
                  <VerificationBadge status={m.verification_status} details={m.verification_details} showReason />
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
      )}

      {tab === "models" && comparison && (
        <section>
          <div className="section-heading">
            <h2>Comparison</h2>
            <button onClick={() => setComparison(null)}>Close</button>
          </div>
          <ModelCompareTable rows={comparison} internetAvailable={comparisonOnline} onLinkRepo={(id, repo) => void handleLinkRepo(id, repo)} />
        </section>
      )}
    </div>
  );
}
