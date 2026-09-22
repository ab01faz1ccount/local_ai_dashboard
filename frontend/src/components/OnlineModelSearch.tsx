import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  apiErrorMessage,
  type DownloadJob,
  type HfFileCandidate,
  type HfModelResult,
} from "../api";
import { formatBytes, formatCount, formatDate, formatSpeed } from "../browse-utils";
import { FileBrowserDialog } from "./FileBrowserDialog";
import "../browse.css";

const POLL_MS = 1000;

/**
 * Search Hugging Face for GGUF models by an approximate name, pick a
 * quantization, and download it for real (resumable, checked against
 * Hugging Face's own SHA256). A finished download is added to the models
 * catalog by the backend, with its Hugging Face link already filled in.
 *
 * `onModelAdded` fires once per finished+registered download so the caller
 * (Settings, the setup wizard) can refresh its own model list.
 */
export function OnlineModelSearch({
  onModelAdded,
  disabled = false,
}: {
  onModelAdded?: (modelId?: number) => void;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<HfModelResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [openRepo, setOpenRepo] = useState<string | null>(null);
  const [filesByRepo, setFilesByRepo] = useState<Record<string, HfFileCandidate[] | "loading" | string>>({});

  const [jobs, setJobs] = useState<DownloadJob[]>([]);
  const [modelsDir, setModelsDir] = useState<string | null>(null);
  const [pickingDir, setPickingDir] = useState(false);

  // Jobs we've already reported to onModelAdded, so a re-poll never fires it twice.
  const reported = useRef<Set<string>>(new Set());
  const onModelAddedRef = useRef(onModelAdded);
  onModelAddedRef.current = onModelAdded;

  useEffect(() => {
    api.getModelsDir().then((d) => setModelsDir(d.path)).catch(() => undefined);
    // Pick up downloads already running (e.g. the user switched tabs and came back).
    api.listDownloads().then((existing) => {
      existing.forEach((j) => {
        if (j.status === "done") reported.current.add(j.id); // finished before we mounted: already added
      });
      setJobs(existing);
    }).catch(() => undefined);
  }, []);

  const hasActive = jobs.some((j) => j.status === "queued" || j.status === "downloading");

  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(async () => {
      try {
        const latest = await api.listDownloads();
        setJobs(latest);
        for (const j of latest) {
          if (j.status === "done" && !reported.current.has(j.id)) {
            reported.current.add(j.id);
            onModelAddedRef.current?.(j.result.model_id);
          }
        }
      } catch {
        // transient; next tick retries
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [hasActive]);

  async function runSearch() {
    if (!query.trim()) return;
    setSearching(true);
    setError(null);
    setOpenRepo(null);
    try {
      setResults(await api.searchHfModels(query.trim()));
    } catch (err) {
      setResults(null);
      setError(apiErrorMessage(err, "Search failed."));
    } finally {
      setSearching(false);
    }
  }

  const toggleRepo = useCallback(
    async (repo: string) => {
      if (openRepo === repo) {
        setOpenRepo(null);
        return;
      }
      setOpenRepo(repo);
      if (filesByRepo[repo] && filesByRepo[repo] !== "loading" && Array.isArray(filesByRepo[repo])) return;
      setFilesByRepo((p) => ({ ...p, [repo]: "loading" }));
      try {
        const files = await api.listHfFiles(repo);
        setFilesByRepo((p) => ({ ...p, [repo]: files }));
      } catch (err) {
        setFilesByRepo((p) => ({ ...p, [repo]: apiErrorMessage(err, "Could not list files.") }));
      }
    },
    [openRepo, filesByRepo]
  );

  async function startDownload(repo: string, filename: string) {
    setError(null);
    try {
      const job = await api.startModelDownload(repo, filename);
      setJobs((prev) => [job, ...prev.filter((j) => j.id !== job.id)]);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not start the download."));
    }
  }

  async function cancel(id: string) {
    try {
      await api.cancelDownload(id);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not cancel."));
    }
  }

  async function changeDir(path: string) {
    setPickingDir(false);
    try {
      const res = await api.setModelsDir(path);
      setModelsDir(res.path);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not use that folder."));
    }
  }

  function jobFor(repo: string, filename: string): DownloadJob | undefined {
    return jobs.find(
      (j) => j.repo_id === repo && j.filename === filename && (j.status === "queued" || j.status === "downloading")
    );
  }

  return (
    <div>
      <div className="browse-meta" style={{ marginBottom: 8 }}>
        Downloads go to: <span className="mono">{modelsDir ?? "…"}</span>{" "}
        <button style={{ fontSize: 12, padding: "2px 8px" }} disabled={disabled} onClick={() => setPickingDir(true)}>
          Change…
        </button>
      </div>

      <div className="browse-search">
        <input
          aria-label="Model name"
          placeholder="Model name, even roughly — e.g. llama 3.2 3b, qwen 2.5 coder"
          value={query}
          disabled={disabled}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void runSearch()}
        />
        <button className="primary" disabled={disabled || searching || !query.trim()} onClick={() => void runSearch()}>
          {searching ? "Searching…" : "Search"}
        </button>
      </div>
      <p className="browse-meta">فقط از Hugging Face می‌گرده، و فقط مدل‌های GGUF (قابل اجرا توی llama.cpp).</p>

      {error && <div className="browse-error">{error}</div>}

      {results && results.length === 0 && <div className="browse-meta">چیزی پیدا نشد — اسم رو ساده‌تر یا کوتاه‌تر بنویس.</div>}

      {results?.map((r) => {
        const files = filesByRepo[r.repo_id];
        return (
          <div className="browse-card" key={r.repo_id}>
            <div className="browse-card-head">
              <div>
                <h4>
                  {r.repo_id}
                  {r.gated && <span className="browse-badge warn">gated</span>}
                </h4>
                <div className="browse-meta">
                  ⬇ {formatCount(r.downloads)} · ♥ {formatCount(r.likes)} · updated {formatDate(r.updated)} ·{" "}
                  <a href={r.url} target="_blank" rel="noreferrer">
                    open on Hugging Face
                  </a>
                </div>
              </div>
              <button onClick={() => void toggleRepo(r.repo_id)}>{openRepo === r.repo_id ? "Hide files" : "Show files"}</button>
            </div>

            {r.gated && (
              <div className="browse-meta" style={{ marginTop: 6 }}>
                این repo نیاز به ورود/پذیرش شرایط توی خود Hugging Face داره؛ از این‌جا قابل دانلود نیست.
              </div>
            )}

            {openRepo === r.repo_id && (
              <div style={{ marginTop: 8 }}>
                {files === "loading" && <div className="browse-meta">Loading files…</div>}
                {typeof files === "string" && files !== "loading" && <div className="browse-error">{files}</div>}
                {Array.isArray(files) && files.length === 0 && (
                  <div className="browse-meta">فایل GGUF قابل اجرایی توی این repo نبود.</div>
                )}
                {Array.isArray(files) &&
                  files.map((f) => {
                    const active = jobFor(r.repo_id, f.filename);
                    return (
                      <div className="browse-file-row" key={f.filename}>
                        <div>
                          <div>
                            {f.display_name}
                            {f.quantization && <span className="browse-badge">{f.quantization}</span>}
                            {f.parts > 1 && <span className="browse-badge">{f.parts} parts</span>}
                            {f.verifiable && <span className="browse-badge ok">SHA256 check</span>}
                          </div>
                          <div className="browse-meta">{formatBytes(f.size_bytes)}</div>
                        </div>
                        <button
                          className="primary"
                          disabled={disabled || r.gated || active != null}
                          onClick={() => void startDownload(r.repo_id, f.filename)}
                        >
                          {active ? "Downloading…" : "Download"}
                        </button>
                      </div>
                    );
                  })}
              </div>
            )}
          </div>
        );
      })}

      {jobs.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h4 style={{ margin: "0 0 4px" }}>Downloads</h4>
          {jobs.map((j) => {
            const pct = j.total_bytes ? Math.min(100, Math.round((j.downloaded_bytes / j.total_bytes) * 100)) : null;
            const active = j.status === "queued" || j.status === "downloading";
            return (
              <div className="browse-card" key={j.id}>
                <div className="browse-card-head">
                  <div>
                    <div>
                      {j.repo_id} · <span className="mono">{j.filename.split("/").pop()}</span>
                    </div>
                    <div className="browse-meta">
                      {j.status === "done" && "Finished"}
                      {j.status === "cancelled" && "Cancelled — asking again resumes from where it stopped"}
                      {j.status === "error" && "Failed"}
                      {active &&
                        `${formatBytes(j.downloaded_bytes)} / ${formatBytes(j.total_bytes)}${pct != null ? ` (${pct}%)` : ""} ${formatSpeed(j.speed_bps)}`}
                      {j.note && ` · ${j.note}`}
                    </div>
                  </div>
                  {active && <button onClick={() => void cancel(j.id)}>Cancel</button>}
                </div>
                {(active || j.status === "done") && (
                  <div className={`browse-progress ${j.status === "done" ? "done" : ""}`}>
                    <div style={{ width: `${j.status === "done" ? 100 : pct ?? 5}%` }} />
                  </div>
                )}
                {j.status === "error" && <div className="browse-error">{j.error}</div>}
                {j.status === "done" && j.result.registered && (
                  <div className="browse-ok">✓ Added to your models{j.result.model_name ? `: ${j.result.model_name}` : ""}</div>
                )}
                {j.status === "done" && j.result.registered === false && (
                  <div className="browse-error">
                    Downloaded, but couldn't be added to the catalog ({j.result.register_error}). It's on disk at{" "}
                    <span className="mono">{j.local_path}</span> — add it from the Offline tab.
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {pickingDir && (
        <FileBrowserDialog
          title="Choose the folder for downloaded models"
          mode="folder"
          initialPath={modelsDir}
          onSelect={(p) => void changeDir(p)}
          onClose={() => setPickingDir(false)}
        />
      )}
    </div>
  );
}
