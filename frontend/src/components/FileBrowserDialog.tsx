import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiErrorMessage, type FsListing } from "../api";
import { formatBytes } from "../browse-utils";
import "../browse.css";

interface FileBrowserDialogProps {
  title: string;
  /** "file": pick one file. "folder": pick the folder you're currently looking at. */
  mode: "file" | "folder";
  /** File mode only, e.g. [".gguf"]. Folders always stay visible so you can navigate. */
  extensions?: string[];
  /** File mode only: hide files that can't be launched. */
  executableOnly?: boolean;
  /** Where to start. A file path works too (its folder is shown). */
  initialPath?: string | null;
  onSelect: (path: string) => void;
  onClose: () => void;
}

/**
 * A file/folder picker that lists the disk through the backend
 * (GET /api/v1/discover/fs/list) instead of using <input type="file">:
 * a browser never reveals a picked file's real path, but this app needs
 * the real path to launch llama-server against it / remember where a
 * model lives. Mount it only while it should be open.
 */
export function FileBrowserDialog({
  title,
  mode,
  extensions,
  executableOnly,
  initialPath,
  onSelect,
  onClose,
}: FileBrowserDialogProps) {
  const [listing, setListing] = useState<FsListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pathInput, setPathInput] = useState(initialPath ?? "");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [showHidden, setShowHidden] = useState(false);

  // Ignore responses that arrive after a newer navigation was started.
  const requestSeq = useRef(0);

  const load = useCallback(
    async (path: string | null, hidden: boolean, fallbackToHome = false) => {
      const seq = ++requestSeq.current;
      setLoading(true);
      setError(null);
      try {
        const result = await api.fsList({
          path,
          kind: mode === "folder" ? "dir" : "file",
          extensions,
          executableOnly,
          showHidden: hidden,
        });
        if (seq !== requestSeq.current) return;
        setListing(result);
        setPathInput(result.path);
        setSelectedFile(null);
      } catch (err) {
        if (seq !== requestSeq.current) return;
        if (fallbackToHome) {
          // e.g. the remembered path was deleted: start from the home folder instead of an error.
          void load(null, hidden);
          return;
        }
        setError(apiErrorMessage(err, "Could not read that folder."));
      } finally {
        if (seq === requestSeq.current) setLoading(false);
      }
    },
    // extensions is an array prop; key on its content so a new-but-equal array doesn't refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mode, executableOnly, (extensions ?? []).join(",")]
  );

  useEffect(() => {
    void load(initialPath ? initialPath : null, showHidden, Boolean(initialPath));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function navigate(path: string) {
    void load(path, showHidden);
  }

  function toggleHidden(next: boolean) {
    setShowHidden(next);
    void load(listing?.path ?? null, next);
  }

  const atDriveList = listing?.path === "";
  const canGoUp = listing != null && listing.parent !== null;
  const folderSelectable = mode === "folder" && listing != null && !atDriveList;

  return (
    <div className="browse-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="browse-dialog" role="dialog" aria-modal="true" aria-label={title}>
        <div className="browse-dialog-header">
          <h3>{title}</h3>
          <button onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="browse-pathbar">
          <button disabled={!canGoUp || loading} onClick={() => listing && listing.parent !== null && navigate(listing.parent)}>
            ↑ Up
          </button>
          <input
            aria-label="Current path"
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && navigate(pathInput)}
            placeholder="Type or paste a path, then press Enter"
          />
          <button disabled={loading} onClick={() => navigate(pathInput)}>
            Go
          </button>
        </div>

        {listing && listing.roots.length > 0 && (
          <div className="browse-roots">
            {listing.roots.map((r) => (
              <button key={r.path} onClick={() => navigate(r.path)}>
                {r.name}
              </button>
            ))}
          </div>
        )}

        <label className="browse-hidden-toggle">
          <input type="checkbox" checked={showHidden} onChange={(e) => toggleHidden(e.target.checked)} />
          Show hidden files
        </label>

        <div className="browse-list" style={{ marginTop: 8 }}>
          {loading && !listing && <div className="browse-empty">Loading…</div>}
          {error && <div className="browse-error" style={{ padding: 10 }}>{error}</div>}
          {listing && !error && listing.entries.length === 0 && (
            <div className="browse-empty">
              {mode === "folder" ? "No sub-folders here." : "Nothing matching here."}
            </div>
          )}
          {listing &&
            !error &&
            listing.entries.map((e) => (
              <button
                key={e.path}
                className={`browse-row ${selectedFile === e.path ? "selected" : ""}`}
                onClick={() => (e.is_dir ? navigate(e.path) : setSelectedFile(e.path))}
                onDoubleClick={() => !e.is_dir && onSelect(e.path)}
              >
                <span aria-hidden>{e.is_dir ? "📁" : "📄"}</span>
                <span className="browse-name">{e.name}</span>
                {!e.is_dir && <span className="browse-size">{formatBytes(e.size)}</span>}
              </button>
            ))}
          {listing?.truncated && (
            <div className="browse-empty">Only the first entries are shown — type a more specific path.</div>
          )}
        </div>

        <div className="browse-dialog-footer">
          <span className="browse-selected" title={mode === "folder" ? listing?.path : selectedFile ?? ""}>
            {mode === "folder" ? (atDriveList ? "Pick a drive first" : listing?.path ?? "") : selectedFile ?? "No file selected"}
          </span>
          <button onClick={onClose}>Cancel</button>
          {mode === "folder" ? (
            <button className="primary" disabled={!folderSelectable} onClick={() => listing && onSelect(listing.path)}>
              Select this folder
            </button>
          ) : (
            <button className="primary" disabled={!selectedFile} onClick={() => selectedFile && onSelect(selectedFile)}>
              Select
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
