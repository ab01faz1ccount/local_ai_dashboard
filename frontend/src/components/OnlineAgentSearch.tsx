import { useEffect, useRef, useState } from "react";
import {
  api,
  apiErrorMessage,
  type AgentBackendInfo,
  type AgentInstallCandidates,
  type AgentInstallJob,
  type AgentRepoResult,
} from "../api";
import { formatCount, formatDate } from "../browse-utils";
import "../browse.css";

const POLL_MS = 1000;

/**
 * Search GitHub for an agent, look at how its README says to install it,
 * and -- only after you've read the exact command and pressed Confirm --
 * run it.
 *
 * What "reliable" means here: the results show signals (stars, whether the
 * owner is an organization, license, last push, archived) so you can judge
 * for yourself. Nothing is labelled "verified", because GitHub can't vouch
 * for a project. Only two shapes of command are ever runnable from here
 * (the backend enforces this again on its side): a plain package-manager
 * install, or a `curl|wget <url> | bash|sh` script installer -- the latter
 * is flagged and named ("downloads & runs a script", with the host it
 * downloads from) so it's never mistaken for something safer than it is.
 * Anything else -- `sudo …`, chained commands -- is shown so you can copy
 * it and run it yourself if you decide to.
 */
export function OnlineAgentSearch({
  disabled = false,
  onInstalled,
}: {
  disabled?: boolean;
  /** Called after an install finishes successfully, with the refreshed backend list. */
  onInstalled?: (backends: AgentBackendInfo[]) => void;
}) {
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<AgentRepoResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [openRepo, setOpenRepo] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<Record<string, AgentInstallCandidates | "loading" | string>>({});

  // The command the user is being asked to confirm right now.
  const [pending, setPending] = useState<{ repo: string; command: string; kind: "package" | "script" | null; host: string | null } | null>(
    null
  );
  const [job, setJob] = useState<AgentInstallJob | null>(null);
  const [detected, setDetected] = useState<AgentBackendInfo[] | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const onInstalledRef = useRef(onInstalled);
  onInstalledRef.current = onInstalled;

  useEffect(() => {
    if (!job || job.status !== "running") return;
    const id = job.id;
    const timer = window.setInterval(async () => {
      try {
        const latest = await api.getAgentInstall(id);
        setJob(latest);
        if (latest.status === "done") {
          const backends = await api.listAgentBackends();
          setDetected(backends);
          onInstalledRef.current?.(backends);
        }
      } catch {
        // transient; next tick retries
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.status]);

  async function runSearch() {
    if (!query.trim()) return;
    setSearching(true);
    setError(null);
    setOpenRepo(null);
    setPending(null);
    try {
      setResults(await api.searchAgentRepos(query.trim()));
    } catch (err) {
      setResults(null);
      setError(apiErrorMessage(err, "Search failed."));
    } finally {
      setSearching(false);
    }
  }

  async function toggleRepo(fullName: string) {
    if (openRepo === fullName) {
      setOpenRepo(null);
      return;
    }
    setOpenRepo(fullName);
    setPending(null);
    const cached = candidates[fullName];
    if (cached && typeof cached !== "string") return;
    setCandidates((p) => ({ ...p, [fullName]: "loading" }));
    try {
      const c = await api.getAgentInstallCandidates(fullName);
      setCandidates((p) => ({ ...p, [fullName]: c }));
    } catch (err) {
      setCandidates((p) => ({ ...p, [fullName]: apiErrorMessage(err, "Could not read the README.") }));
    }
  }

  async function confirmInstall() {
    if (!pending) return;
    setError(null);
    setDetected(null);
    try {
      setJob(await api.installAgent(pending.repo, pending.command));
      setPending(null);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not start the install."));
    }
  }

  async function copy(command: string) {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(command);
      window.setTimeout(() => setCopied((c) => (c === command ? null : c)), 1500);
    } catch {
      setError("Couldn't copy automatically — select the command and copy it by hand.");
    }
  }

  const installing = job?.status === "running";

  return (
    <div>
      <div className="browse-search">
        <input
          aria-label="Agent name"
          placeholder="Agent name, even roughly — e.g. hermes agent"
          value={query}
          disabled={disabled}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void runSearch()}
        />
        <button className="primary" disabled={disabled || searching || !query.trim()} onClick={() => void runSearch()}>
          {searching ? "Searching…" : "Search"}
        </button>
      </div>
      <p className="browse-meta">
        فقط توی GitHub می‌گرده. ستاره، مالک (سازمان یا شخص)، لایسنس و آخرین فعالیت رو نشون می‌ده تا خودت قضاوت کنی — «تاییدشده» بودن رو هیچ‌کس تضمین
        نمی‌کنه.
      </p>

      {error && <div className="browse-error">{error}</div>}
      {results && results.length === 0 && <div className="browse-meta">چیزی پیدا نشد.</div>}

      {results?.map((r) => {
        const cand = candidates[r.full_name];
        return (
          <div className="browse-card" key={r.full_name}>
            <div className="browse-card-head">
              <div>
                <h4>
                  {r.full_name}
                  {r.owner_type === "Organization" && <span className="browse-badge">organization</span>}
                  {r.archived && <span className="browse-badge warn">archived</span>}
                </h4>
                {r.description && <div style={{ fontSize: 13 }}>{r.description}</div>}
                <div className="browse-meta">
                  ★ {formatCount(r.stars)} · {r.license ?? "no license listed"} · {r.language ?? "—"} · last push {formatDate(r.last_push)} ·{" "}
                  {r.url && (
                    <a href={r.url} target="_blank" rel="noreferrer">
                      open on GitHub
                    </a>
                  )}
                </div>
              </div>
              <button onClick={() => void toggleRepo(r.full_name)}>
                {openRepo === r.full_name ? "Hide" : "Install options"}
              </button>
            </div>

            {openRepo === r.full_name && (
              <div style={{ marginTop: 8 }}>
                {cand === "loading" && <div className="browse-meta">Reading the README…</div>}
                {typeof cand === "string" && cand !== "loading" && <div className="browse-error">{cand}</div>}
                {cand && typeof cand !== "string" && cand.commands.length === 0 && (
                  <div className="browse-meta">
                    توی README دستور نصب مشخصی پیدا نشد.{" "}
                    <a href={cand.repo_url} target="_blank" rel="noreferrer">
                      README رو خودت ببین
                    </a>
                  </div>
                )}
                {cand &&
                  typeof cand !== "string" &&
                  cand.commands.map((c) => (
                    <div className="browse-file-row" key={c.command} style={{ alignItems: "flex-start" }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div className="browse-command">
                          {c.command}
                          {c.kind === "script" && <span className="browse-badge warn">downloads &amp; runs a script</span>}
                        </div>
                        {c.kind === "script" && c.host && <div className="browse-meta">از: {c.host}</div>}
                        {c.notes.map((n) => (
                          <div className="browse-meta" key={n}>
                            {n}
                          </div>
                        ))}
                        {!c.runnable && <div className="browse-meta">خودت توی ترمینال اجراش کن — {c.reason}</div>}
                      </div>
                      {c.runnable ? (
                        <button
                          className="primary"
                          disabled={disabled || installing}
                          onClick={() => setPending({ repo: r.full_name, command: c.command, kind: c.kind, host: c.host })}
                        >
                          Install…
                        </button>
                      ) : (
                        <button onClick={() => void copy(c.command)}>{copied === c.command ? "Copied" : "Copy"}</button>
                      )}
                    </div>
                  ))}

                {pending && pending.repo === r.full_name && (
                  <div className="browse-confirm" role="alertdialog" aria-label="Confirm install">
                    <div style={{ fontSize: 13, marginBottom: 6 }}>
                      {pending.kind === "script" ? (
                        <>
                          این یه اسکریپت نصبه: از <b>{pending.host}</b> دانلود می‌شه و مستقیم روی سیستمت اجرا می‌شه — یعنی هر کاری توی اون
                          اسکریپت نوشته باشه انجام می‌ده. این دستور دقیقاً همین‌طوری اجرا می‌شه و کدی از <b>{r.full_name}</b> رو نصب می‌کنه.
                        </>
                      ) : (
                        <>
                          این دستور دقیقاً همین‌طوری روی سیستمت اجرا می‌شه و کدی از <b>{r.full_name}</b> رو نصب می‌کنه.
                        </>
                      )}{" "}
                      اگه به این پروژه اعتماد داری تایید کن:
                    </div>
                    <div className="browse-command">{pending.command}</div>
                    <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                      <button className="primary" onClick={() => void confirmInstall()}>
                        Confirm &amp; install
                      </button>
                      <button onClick={() => setPending(null)}>Cancel</button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}

      {job && (
        <div className="browse-card">
          <div className="browse-card-head">
            <div>
              <h4>
                Install: <span className="mono">{job.command}</span>
              </h4>
              <div className="browse-meta">
                {job.status === "running" && "Running…"}
                {job.status === "done" && "Finished"}
                {job.status === "error" && "Failed"}
              </div>
            </div>
          </div>
          {job.log.length > 0 && <pre className="browse-log mono">{job.log.slice(-60).join("\n")}</pre>}
          {job.status === "error" && job.error && <div className="browse-error">{job.error}</div>}
          {job.status === "done" && detected && (
            <div className="browse-ok">
              ✓ نصب تموم شد. Agentهای شناخته‌شده الان:{" "}
              {detected
                .filter((b) => b.backend_id !== "generic")
                .map((b) => `${b.display_name} ${b.detected ? "(detected ✓)" : "(not detected)"}`)
                .join("، ") || "—"}
              . اگه پیدا نشد، از تب Offline مسیر فایلش رو بده.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
