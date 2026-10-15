import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  apiErrorMessage,
  type DownloadJob,
  type HfFileCandidate,
  type HfModelResult,
  type HfSearchResponse,
  type SearchPurpose,
} from "../api";
import { formatBytes, formatCount, formatDate, formatSpeed } from "../browse-utils";
import { recommendedFirst } from "../connect-utils";
import {
  DEFAULT_FILTERS,
  PURPOSES,
  SIZE_BUCKETS,
  SORTS,
  activeFilterCount,
  applyFilters,
  filesForQuery,
  followersText,
  huggingFaceSearchUrl,
  quantMatches,
  sortResults,
  splitByRole,
  trustBadge,
  type SearchFilters,
  type SortKey,
} from "../search-utils";
import { FIT_TEXT, FIT_TONE } from "../context-utils";
import { FitBadge } from "./FitBadge";
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
  const [response, setResponse] = useState<HfSearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [purpose, setPurpose] = useState<SearchPurpose>("any");
  const [filters, setFilters] = useState<SearchFilters>(DEFAULT_FILTERS);
  const [sort, setSort] = useState<SortKey>("best");
  const results = response?.results ?? null;
  const wantedQuant = response?.query.quant ?? null;

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

  async function runSearch(forPurpose: SearchPurpose = purpose) {
    if (!query.trim()) return;
    setSearching(true);
    setError(null);
    setOpenRepo(null);
    try {
      setResponse(
        forPurpose === "any" ? await api.searchHfModels(query.trim()) : await api.searchHfModels(query.trim(), forPurpose)
      );
    } catch (err) {
      setResponse(null);
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

  const shown = results ? sortResults(applyFilters(results, filters), sort) : null;
  const { main, variants } = shown ? splitByRole(shown) : { main: [], variants: [] };
  const hiddenByFilters = results && shown ? results.length - shown.length : 0;

  function renderCard(r: HfModelResult, top: boolean) {
    const files = filesByRepo[r.repo_id];
    const badge = trustBadge(r);
    const followers = followersText(r.trust?.followers);
    const hint = r.fit_hint;
    return (
      <div className={`browse-card result-card ${top ? "top-pick" : ""}`} key={r.repo_id}>
        <div className="browse-card-head">
          <div className="result-main">
            <h4>
              {r.repo_id}
              {top && <span className="browse-badge ok">Best match</span>}
              {badge && <span className={`browse-badge ${badge.tone === "ok" ? "ok" : "info"}`}>{badge.text}</span>}
              {r.gated && <span className="browse-badge warn">gated</span>}
            </h4>
            <div className="browse-meta result-stats">
              <span title="Downloads in the last 30 days">⬇ {formatCount(r.downloads)}</span>
              <span>♥ {formatCount(r.likes)}</span>
              {followers ? (
                <span title={`${r.author}'s followers on Hugging Face`}>{followers}</span>
              ) : (
                <span className="muted" title="Hugging Face did not report a follower count for this publisher">
                  followers n/a
                </span>
              )}
              <span>updated {formatDate(r.updated)}</span>
              {hint && hint.label && hint.label !== "UNKNOWN" && (
                <span
                  className="fit-badge"
                  data-tone={FIT_TONE[hint.label]}
                  title="Estimated from the parameter count in the name, at a typical Q4 size. Open the files for the exact verdict per file."
                >
                  ≈ {formatBytes(hint.approx_q4_bytes)} at Q4 · {FIT_TEXT[hint.label]}
                  {hint.target === "gpu" ? " · GPU" : hint.target === "cpu" ? " · CPU" : ""}
                </span>
              )}
              <a href={r.url} target="_blank" rel="noreferrer">
                open on Hugging Face
              </a>
            </div>
            {r.why && r.why.length > 0 && <div className="result-why">{r.why.join(" · ")}</div>}
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
              wantedQuant &&
              !files.some((f) => quantMatches(f.quantization, wantedQuant)) &&
              files.length > 0 && (
                <div className="browse-meta">No file here is {wantedQuant}; the closest options are listed below.</div>
              )}
            {Array.isArray(files) &&
              filesForQuery(recommendedFirst(files), wantedQuant).map((f) => {
                const active = jobFor(r.repo_id, f.filename);
                return (
                  <div className="browse-file-row" key={f.filename}>
                    <div>
                      <div>
                        {f.display_name}
                        {f.quantization && <span className="browse-badge">{f.quantization}</span>}
                        {quantMatches(f.quantization, wantedQuant) && (
                          <span className="browse-badge ok">Matches your {wantedQuant}</span>
                        )}
                        {f.parts > 1 && <span className="browse-badge">{f.parts} parts</span>}
                        {f.verifiable && <span className="browse-badge ok">SHA256 check</span>}
                        {f.recommended && <span className="browse-badge ok">Recommended for this PC</span>}
                      </div>
                      <div className="browse-meta">
                        {formatBytes(f.size_bytes)}
                        {f.hardware_fit && (
                          <>
                            {" · "}
                            <FitBadge fit={f.hardware_fit} />
                          </>
                        )}
                      </div>
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
  }

  return (
    <div className="online-search">
      <div className="folder-bar" role="group" aria-label="Download folder">
        <svg className="folder-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />
        </svg>
        <div className="folder-text">
          <span className="folder-label">Save downloads to</span>
          <span className="mono folder-path" title={modelsDir ?? ""}>
            {modelsDir ?? "…"}
          </span>
        </div>
        <button className="folder-change" disabled={disabled} onClick={() => setPickingDir(true)}>
          Choose folder…
        </button>
      </div>

      <div className="browse-search">
        <input
          aria-label="Model name"
          placeholder="Model name, even roughly — e.g. qwen3.5 9b q6_k_m, llama 3.2 3b"
          value={query}
          disabled={disabled}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void runSearch()}
        />
        <button className="primary" disabled={disabled || searching || !query.trim()} onClick={() => void runSearch()}>
          {searching ? "Searching…" : "Search"}
        </button>
      </div>

      <div className="chip-row" role="group" aria-label="What it is for">
        <span className="chip-label">Good for</span>
        {PURPOSES.map((p) => (
          <button
            key={p.id}
            className={`chip ${purpose === p.id ? "on" : ""}`}
            aria-pressed={purpose === p.id}
            title={p.hint}
            disabled={disabled}
            onClick={() => {
              setPurpose(p.id);
              if (response) void runSearch(p.id);
            }}
          >
            {p.label}
          </button>
        ))}
      </div>
      <p className="browse-meta">
        فقط از Hugging Face می‌گرده، و فقط مدل‌های GGUF (قابل اجرا توی llama.cpp). «Good for» فقط امتیاز می‌ده، چیزی رو حذف نمی‌کنه.
      </p>

      {response && (
        <div className="understood" aria-live="polite">
          {response.query.corrected_from && (
            <span>
              Showing results for <strong>{response.query.text}</strong> (you typed “{response.query.corrected_from}”).{" "}
            </span>
          )}
          Searched for <strong>{response.query.text}</strong>
          {response.query.size_b != null && <> · size {response.query.size_b}B</>}
          {response.query.quant && (
            <>
              {" "}
              · quantization <strong>{response.query.quant}</strong> (it is a file inside a repo, so it is matched when you open the files)
            </>
          )}
        </div>
      )}

      {error && <div className="browse-error">{error}</div>}

      {results && results.length > 0 && (
        <div className="filter-bar" role="group" aria-label="Filters">
          <button
            className={`chip ${filters.trustedOnly ? "on" : ""}`}
            aria-pressed={filters.trustedOnly}
            title="Official teams and well-known GGUF publishers"
            onClick={() => setFilters((f) => ({ ...f, trustedOnly: !f.trustedOnly }))}
          >
            Trusted publishers only
          </button>
          <button
            className={`chip ${filters.hideGated ? "on" : ""}`}
            aria-pressed={filters.hideGated}
            onClick={() => setFilters((f) => ({ ...f, hideGated: !f.hideGated }))}
          >
            Hide gated
          </button>
          <label className="filter-select">
            Size
            <select
              aria-label="Size"
              value={filters.size}
              onChange={(e) => setFilters((f) => ({ ...f, size: e.target.value as SearchFilters["size"] }))}
            >
              {SIZE_BUCKETS.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.label}
                </option>
              ))}
            </select>
          </label>
          <label className="filter-select">
            Sort
            <select aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
              {SORTS.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          {activeFilterCount(filters) > 0 && (
            <button className="chip-reset" onClick={() => setFilters(DEFAULT_FILTERS)}>
              Clear filters
            </button>
          )}
        </div>
      )}

      {results && results.length === 0 && (
        <div className="browse-meta">
          چیزی پیدا نشد — اسم رو ساده‌تر یا کوتاه‌تر بنویس.{" "}
          <a href={huggingFaceSearchUrl(response?.query.text ?? query)} target="_blank" rel="noreferrer">
            Search on Hugging Face yourself
          </a>
        </div>
      )}
      {results && results.length > 0 && shown && shown.length === 0 && (
        <div className="browse-meta">The filters hide all {results.length} results. Clear a filter to see them.</div>
      )}

      {main.map((r, i) => renderCard(r, i === 0 && sort === "best" && r.role === "conversion"))}

      {variants.length > 0 && (
        <details className="variants">
          <summary>
            {variants.length} modified version{variants.length === 1 ? "" : "s"} (fine-tunes, uncensored, merges…)
          </summary>
          <p className="browse-meta">
            Names with extra words usually mean someone changed the model. They can be good, but they are not the
            model you searched for, so they are kept apart.
          </p>
          {variants.map((r) => renderCard(r, false))}
        </details>
      )}

      {results && results.length > 0 && (
        <p className="browse-meta">
          {hiddenByFilters > 0 && `${hiddenByFilters} hidden by filters · `}
          Not what you wanted?{" "}
          <a href={huggingFaceSearchUrl(response?.query.text ?? query)} target="_blank" rel="noreferrer">
            See the same search on Hugging Face
          </a>
        </p>
      )}

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
