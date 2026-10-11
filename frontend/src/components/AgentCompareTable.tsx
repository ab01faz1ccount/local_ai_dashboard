import { useState } from "react";
import { api, apiErrorMessage, type AgentBackendInfo, type AgentProfileResult, type AgentRepoResult, type AgentSummary } from "../api";
import { factKeysInOrder, findFact, safeSourceUrl } from "../knowledge-utils";
import { FactCell } from "./FactCell";

interface Props {
  agents: AgentSummary[];
  backends: AgentBackendInfo[];
  /** One entry per agent id; missing = still loading. */
  profiles: Record<number, AgentProfileResult | undefined>;
  /** The user linked a repository to an agent. */
  onLinked: (agentId: number) => void;
}

const REPO_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9][A-Za-z0-9._-]*$/;

/** What an agent is and can do, from its GitHub repository, next to what this
 * machine has of it. An agent whose repository is not known yet asks for it
 * (typed, or picked from a GitHub search) rather than guessing. */
export function AgentCompareTable({ agents, backends, profiles, onLinked }: Props) {
  if (agents.length === 0) return null;
  const rows = factKeysInOrder(agents.map((a) => profiles[a.id]?.profile));
  const backendOf = (a: AgentSummary) => backends.find((b) => b.backend_id === a.agent_backend);

  return (
    <div className="compare-table-wrap">
      <table className="compare-table">
        <thead>
          <tr>
            <th>Agent</th>
            {agents.map((a) => (
              <th key={a.id}>{a.name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr className="compare-section-row">
            <td colSpan={agents.length + 1}>On this computer</td>
          </tr>
          <tr>
            <td>Backend</td>
            {agents.map((a) => (
              <td key={a.id}>{backendOf(a)?.display_name ?? a.agent_backend}</td>
            ))}
          </tr>
          <tr>
            <td>Installed</td>
            {agents.map((a) => {
              const b = backendOf(a);
              return (
                <td key={a.id}>
                  {a.agent_backend === "generic" ? (
                    <span className="fact-missing">Chat only — no external program</span>
                  ) : b?.detected ? (
                    <>✓ {b.detected_version ?? "found"}</>
                  ) : (
                    <span className="fact-missing">Not found on this computer</span>
                  )}
                </td>
              );
            })}
          </tr>

          <tr className="compare-section-row">
            <td colSpan={agents.length + 1}>From GitHub — every fact links to its source</td>
          </tr>
          <tr>
            <td>Repository</td>
            {agents.map((a) => (
              <td key={a.id}>
                <RepoCell agent={a} result={profiles[a.id]} onLinked={() => onLinked(a.id)} />
              </td>
            ))}
          </tr>
          {rows.map(({ key, label }) => (
            <tr key={key}>
              <td>{label}</td>
              {agents.map((a) => (
                <td key={a.id}>
                  {profiles[a.id]?.profile ? <FactCell fact={findFact(profiles[a.id]!.profile, key)} /> : <span className="fact-missing">—</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RepoCell({ agent, result, onLinked }: { agent: AgentSummary; result: AgentProfileResult | undefined; onLinked: () => void }) {
  const [draft, setDraft] = useState("");
  const [found, setFound] = useState<AgentRepoResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function link(repo: string) {
    setBusy(true);
    setError(null);
    try {
      await api.setAgentRepo(agent.id, repo);
      setFound(null);
      setDraft("");
      onLinked();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not save that."));
    } finally {
      setBusy(false);
    }
  }

  async function search() {
    setBusy(true);
    setError(null);
    try {
      setFound((await api.searchAgentRepos(agent.name)).slice(0, 5));
    } catch (err) {
      setError(apiErrorMessage(err, "Search failed."));
    } finally {
      setBusy(false);
    }
  }

  if (!result) return <span className="muted">Loading…</span>;

  if (result.status === "ok" || result.status === "offline" || result.status === "error") {
    const link_ = safeSourceUrl(result.profile?.url ?? (result.repo ? `https://github.com/${result.repo}` : null));
    return (
      <div className="fact-cell">
        {link_ ? (
          <a className="fact-source" href={link_} target="_blank" rel="noreferrer noopener">
            {result.repo} ↗
          </a>
        ) : (
          <span>{result.repo}</span>
        )}
        {result.repo_origin === "saved" && <div className="fact-meta"><span className="fact-tag">linked by you</span></div>}
        {result.status === "offline" && <div className="fact-missing">Offline — can't look this up</div>}
        {result.status === "error" && <div className="error-text">{result.error ?? "Lookup failed."}</div>}
      </div>
    );
  }

  // unlinked
  return (
    <div className="fact-cell">
      <span className="fact-missing">Not linked to a GitHub repository yet</span>
      <div className="repo-link-form">
        <input
          aria-label={`GitHub repository for ${agent.name}`}
          placeholder="owner/name"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button disabled={busy || !REPO_RE.test(draft.trim())} onClick={() => void link(draft.trim())}>
          Link
        </button>
        <button disabled={busy} onClick={() => void search()}>
          Search GitHub
        </button>
      </div>
      {found && (
        <ul className="repo-found">
          {found.length === 0 && <li className="fact-missing">Nothing found for “{agent.name}”.</li>}
          {found.map((r) => (
            <li key={r.full_name}>
              <button className="link-button" disabled={busy} onClick={() => void link(r.full_name)}>
                {r.full_name}
              </button>{" "}
              <span className="muted">★ {r.stars.toLocaleString()}</span>
              {r.description && <div className="muted repo-found-desc">{r.description.slice(0, 110)}</div>}
            </li>
          ))}
        </ul>
      )}
      {error && <div className="error-text">{error}</div>}
    </div>
  );
}
