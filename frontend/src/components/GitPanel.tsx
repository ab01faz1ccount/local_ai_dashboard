import { useEffect, useState } from "react";
import { api, type GitDiffStat, type GitLog, type GitStatus, type ProjectSummary } from "../api";

/**
 * A GUI over the real `git` CLI for one project -- not a reimplementation
 * of it. Every number here (files changed, insertions/deletions, commit
 * log) comes straight from `git`'s own output; see backend/core/git_panel.py.
 * If the project isn't a repo yet (or has no local_path at all), this
 * renders the reason plainly instead of pretending there's nothing to
 * show.
 */
export function GitPanel({ project, onProjectUpdated }: { project: ProjectSummary; onProjectUpdated: (p: ProjectSummary) => void }) {
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [diff, setDiff] = useState<GitDiffStat | null>(null);
  const [log, setLog] = useState<GitLog | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pathDraft, setPathDraft] = useState(project.local_path ?? "");
  const [savingPath, setSavingPath] = useState(false);
  const [commitMessage, setCommitMessage] = useState("");
  const [committing, setCommitting] = useState(false);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const [s, d, l] = await Promise.all([
        api.getProjectGitStatus(project.id),
        api.getProjectGitDiff(project.id),
        api.getProjectGitLog(project.id),
      ]);
      setStatus(s);
      setDiff(d);
      setLog(l);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load git data.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  async function savePath() {
    setSavingPath(true);
    try {
      const updated = await api.updateProject(project.id, { local_path: pathDraft.trim() });
      onProjectUpdated(updated);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the folder path.");
    } finally {
      setSavingPath(false);
    }
  }

  async function handleCommit() {
    if (!commitMessage.trim()) return;
    setCommitting(true);
    setError(null);
    try {
      await api.commitProjectGit(project.id, commitMessage.trim());
      setCommitMessage("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Commit failed.");
    } finally {
      setCommitting(false);
    }
  }

  if (loading && !status) {
    return <p className="muted">در حال بارگذاری...</p>;
  }

  if (status && !status.available) {
    return (
      <div className="panel git-unavailable">
        <p>{status.message}</p>
        {status.reason === "no_local_path" && (
          <div className="hf-link-editor">
            <input
              style={{ flex: 1 }}
              placeholder="/path/to/project"
              value={pathDraft}
              onChange={(e) => setPathDraft(e.target.value)}
            />
            <button className="primary" disabled={!pathDraft.trim() || savingPath} onClick={() => void savePath()}>
              {savingPath ? "..." : "Save"}
            </button>
          </div>
        )}
        {status.reason === "not_a_repo" && (
          <p className="muted mono">git init</p>
        )}
      </div>
    );
  }

  return (
    <div className="git-panel">
      {error && <div className="error-text">{error}</div>}

      <div className="git-status-row">
        <span className="mono">{status?.branch ?? "(detached HEAD)"}</span>
        {status && (status.ahead ?? 0) > 0 && <span className="muted">↑{status.ahead}</span>}
        {status && (status.behind ?? 0) > 0 && <span className="muted">↓{status.behind}</span>}
        <span className="muted">{status?.staged_count ?? 0} staged</span>
        <span className="muted">{status?.unstaged_count ?? 0} unstaged</span>
        <span className="muted">{status?.untracked_count ?? 0} untracked</span>
      </div>

      <div className="panel git-diff-summary">
        <div className="section-heading">
          <h3>Working tree changes</h3>
        </div>
        {diff && diff.files && diff.files.length > 0 ? (
          <>
            <p className="mono">
              {diff.files_changed} files changed, +{diff.insertions} / -{diff.deletions}
            </p>
            <ul className="compare-eval-list">
              {diff.files.map((f) => (
                <li key={f.path}>
                  {f.path}: {f.binary ? "binary" : `+${f.insertions} / -${f.deletions}`}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="muted">هیچ تغییر track‌شده‌ای نیست.</p>
        )}
      </div>

      <div className="panel git-commit-box">
        <div className="section-heading">
          <h3>Commit</h3>
        </div>
        <input
          style={{ width: "100%" }}
          placeholder="commit message"
          value={commitMessage}
          onChange={(e) => setCommitMessage(e.target.value)}
        />
        <button
          className="primary"
          disabled={!commitMessage.trim() || committing}
          onClick={() => void handleCommit()}
        >
          {committing ? "Committing…" : "Stage all & commit"}
        </button>
      </div>

      <div className="panel git-log">
        <div className="section-heading">
          <h3>History</h3>
        </div>
        {log && log.commits && log.commits.length > 0 ? (
          <ul className="git-log-list">
            {log.commits.map((c) => (
              <li key={c.hash}>
                <span className="mono">{c.short_hash}</span> {c.subject}
                <span className="muted"> — {c.author}, {new Date(c.date).toLocaleString()}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">هیچ commit‌ای هنوز ثبت نشده.</p>
        )}
      </div>
    </div>
  );
}
