"""
backend/core/model_discovery.py

The "online" half of Settings -> Browse for models: type an approximate
name, get real Hugging Face GGUF repos back, pick one file (quantization),
and it downloads for real -- resumable, disk-space-checked, and verified
against Hugging Face's own SHA256 for that file.

Scope decisions worth knowing:
  - Hugging Face only, for now (the only trusted source the project has
    named). Every request and every redirect is pinned to its hosts via
    core/net_util.py.
  - Gated repos (login / license acceptance required) are listed but not
    downloadable from here -- doing that would mean handling the user's
    Hugging Face credentials, which this app doesn't.
  - Download state is in memory. If the backend restarts mid-download the
    `.part` file stays on disk and asking for the same file again resumes
    from where it stopped.
  - This module never touches the database. Registering a finished
    download into the models catalog is a callback the API layer passes
    in, so this file stays testable on its own.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

from . import model_search
from .net_util import DiscoveryError, get_json, open_url

HF_HOST = "huggingface.co"
HF_ALLOWED_HOSTS = ("huggingface.co", "hf.co")  # hf.co = Hugging Face's own CDN domain
REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
SEARCH_LIMIT = 12
CHUNK_BYTES = 1024 * 1024
DISK_HEADROOM_BYTES = 256 * 1024 * 1024

_SHARD_RE = re.compile(r"^(?P<base>.+)-(?P<idx>\d{5})-of-(?P<total>\d{5})\.gguf$", re.IGNORECASE)
_QUANT_RE = re.compile(
    r"(?<![A-Za-z0-9])(IQ\d_[A-Z0-9]+|Q\d_K_[SML]|Q\d_K|Q\d_[01]|BF16|F16|F32|MXFP4)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_PARAMS_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)[Bb](?![A-Za-z])")


# ---------------------------------------------------------------------
# Small parsing helpers (pure -- unit-tested without any network)
# ---------------------------------------------------------------------

def guess_quantization(filename: str) -> Optional[str]:
    m = _QUANT_RE.search(Path(filename).name)
    return m.group(1).upper() if m else None


def guess_param_count(repo_id: str) -> Optional[str]:
    m = _PARAMS_RE.search(repo_id.split("/", 1)[-1])
    return f"{m.group(1)}B" if m else None


def _tokens(query: str) -> list[str]:
    return re.findall(r"[a-z0-9.]+", query.lower())


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _relevance(repo_id: str, tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    hay = _squash(repo_id)
    hits = sum(1 for t in tokens if _squash(t) and _squash(t) in hay)
    return hits / len(tokens)


def _query_variants(tokens: list[str]) -> list[str]:
    """The query as typed is tried first; if that finds too little, looser
    spellings are tried, so "llama 3.2 3b" and "llama3.2-3b" both land on
    the same repos even though Hugging Face's own search is strict about
    word boundaries."""
    variants = [" ".join(tokens)]
    if len(tokens) > 1:
        variants.append("-".join(tokens))
        variants.append("".join(tokens))
        variants.append(max(tokens, key=len))
    seen: set[str] = set()
    return [v for v in variants if v and not (v in seen or seen.add(v))]


# ---------------------------------------------------------------------
# Search + file listing
# ---------------------------------------------------------------------

FOLLOWER_LOOKUPS = 8  # distinct publishers looked up per search
FOLLOWER_TTL_S = 24 * 3600
_follower_cache: dict[str, tuple[float, Optional[int]]] = {}
_follower_lock = threading.Lock()


def fetch_followers(author: str) -> Optional[int]:
    """Follower count of a Hugging Face publisher, or None when it can't be
    read. A publisher is either an organization or a user, so both public
    overview endpoints are tried; whatever comes back that isn't a plain
    number is treated as "not published" and nothing is guessed."""
    key = author.lower()
    now = time.time()
    with _follower_lock:
        hit = _follower_cache.get(key)
        if hit and now - hit[0] < FOLLOWER_TTL_S:
            return hit[1]
    value: Optional[int] = None
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", author):
        for kind in ("organizations", "users"):
            try:
                data = get_json(f"https://{HF_HOST}/api/{kind}/{quote(author)}/overview", allowed_hosts=HF_ALLOWED_HOSTS)
            except DiscoveryError:
                continue
            if isinstance(data, dict):
                for field_name in ("numFollowers", "followerCount", "followers"):
                    v = data.get(field_name)
                    if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                        value = v
                        break
            if value is not None:
                break
    with _follower_lock:
        _follower_cache[key] = (now, value)
    return value


def _followers_for(authors: list[str]) -> dict[str, Optional[int]]:
    unique = list(dict.fromkeys(a for a in authors))[:FOLLOWER_LOOKUPS]
    out: dict[str, Optional[int]] = {}

    def work(a: str) -> None:
        try:
            out[a] = fetch_followers(a)
        except Exception:  # noqa: BLE001 - decoration only; never break a search
            out[a] = None

    threads = [threading.Thread(target=work, args=(a,), daemon=True) for a in unique]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=8)
    return out


def _hf_hits(tokens: list[str], limit: int) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for i, variant in enumerate(_query_variants(tokens)):
        # Stop widening once there's enough to choose from; the first
        # (exact) variant almost always suffices for a well-spelled name.
        if i > 0 and len(merged) >= limit:
            break
        url = (
            f"https://{HF_HOST}/api/models?search={quote(variant)}"
            f"&filter=gguf&sort=downloads&direction=-1&limit=30"
        )
        items = get_json(url, allowed_hosts=HF_ALLOWED_HOSTS)
        if not isinstance(items, list):
            continue
        for item in items:
            repo_id = item.get("id") or item.get("modelId")
            if not isinstance(repo_id, str) or not REPO_ID_RE.match(repo_id) or item.get("private"):
                continue
            if repo_id in merged:
                continue
            gated = item.get("gated") not in (None, False)
            merged[repo_id] = {
                "repo_id": repo_id,
                "author": repo_id.split("/", 1)[0],
                "name": repo_id.split("/", 1)[1],
                "downloads": item.get("downloads") or 0,
                "likes": item.get("likes") or 0,
                "updated": item.get("lastModified"),
                "gated": gated,
                "url": f"https://{HF_HOST}/{repo_id}",
                "relevance": round(_relevance(repo_id, tokens), 3),
            }
    return merged


def search_models_full(
    query: str,
    limit: int = SEARCH_LIMIT,
    *,
    purpose: Optional[str] = None,
    hardware: Optional[dict] = None,
    with_followers: bool = True,
) -> dict:
    """{"query": what was understood, "results": ranked hits}.

    Ranking is model_search.score_repo: name match, a penalty for extra
    words (modified versions), publisher trust, downloads, likes and
    followers. Nothing is filtered out on purpose or quantization; those only
    add points."""
    parsed = model_search.parse_query(query)
    if not parsed["tokens"]:
        raise DiscoveryError("یه اسم (حتی تقریبی) برای مدل بنویس.", "bad_input")

    tokens = parsed["tokens"]
    merged = _hf_hits(tokens, limit)

    # Too little back usually means a typo in the family name ("qwwn").
    if len(merged) < 3:
        fixed, changed = model_search.correct_tokens(tokens)
        if changed:
            more = _hf_hits(fixed, limit)
            if more:
                parsed = {**parsed, "tokens": fixed, "text": " ".join(fixed), "corrected_from": " ".join(tokens)}
                merged = {**more, **merged}
                tokens = fixed

    if purpose not in model_search.PURPOSES:
        purpose = None

    items = list(merged.values())
    # Score on the first pass without followers to decide who is worth a lookup.
    first = model_search.rank([model_search.score_repo(i, parsed, None, purpose) for i in items])
    followers: dict[str, Optional[int]] = {}
    if with_followers and first:
        followers = _followers_for([r["author"] for r in first[: limit + 4]])
    scored = model_search.rank(
        [model_search.score_repo(i, parsed, followers.get(i["author"]), purpose) for i in items]
    )[:limit]
    for r in scored:
        r["fit_hint"] = model_search.fit_hint(r["params_b"], hardware)
    return {
        "query": {
            "text": parsed["text"],
            "size_b": parsed["size_b"],
            "quant": parsed["quant"],
            "purpose": purpose or "any",
            "corrected_from": parsed["corrected_from"],
        },
        "results": scored,
    }


def search_models(query: str, limit: int = SEARCH_LIMIT, **kwargs) -> list[dict]:
    """The ranked hits only (no follower lookups unless asked)."""
    kwargs.setdefault("with_followers", False)
    return search_models_full(query, limit, **kwargs)["results"]


@dataclass
class RepoFile:
    path: str
    size: Optional[int]
    sha256: Optional[str]


def list_gguf_candidates(repo_id: str) -> list[dict]:
    """GGUF files in a repo, one candidate per *runnable* model: a
    single .gguf is one candidate; a split model (`-00001-of-00003.gguf`
    ...) is one candidate made of all its parts, and only when every part
    is actually present (a partial set can't be loaded by llama.cpp)."""
    if not REPO_ID_RE.match(repo_id):
        raise DiscoveryError("اسم repo معتبر نیست (باید به شکل author/name باشه).", "bad_input")

    url = f"https://{HF_HOST}/api/models/{quote(repo_id, safe='/')}/tree/main?recursive=true"
    entries = get_json(url, allowed_hosts=HF_ALLOWED_HOSTS)
    if not isinstance(entries, list):
        raise DiscoveryError("لیست فایل‌های این repo قابل خوندن نبود.", "error")

    singles: list[RepoFile] = []
    shard_groups: dict[tuple[str, str, int], dict[int, RepoFile]] = {}

    for e in entries:
        if e.get("type") != "file":
            continue
        path = e.get("path", "")
        if not path.lower().endswith(".gguf"):
            continue
        base_name = Path(path).name
        if base_name.lower().startswith("mmproj"):
            continue  # multimodal projector, not a standalone model
        lfs = e.get("lfs") or {}
        rf = RepoFile(path=path, size=lfs.get("size") or e.get("size"), sha256=lfs.get("oid"))

        m = _SHARD_RE.match(base_name)
        if m:
            key = (str(Path(path).parent), m.group("base"), int(m.group("total")))
            shard_groups.setdefault(key, {})[int(m.group("idx"))] = rf
        else:
            singles.append(rf)

    candidates: list[dict] = []
    for rf in singles:
        candidates.append(_candidate(repo_id, rf.path, Path(rf.path).stem, [rf]))

    for (_dir, base, total), parts in shard_groups.items():
        if set(parts) != set(range(1, total + 1)):
            continue
        ordered = [parts[i] for i in range(1, total + 1)]
        candidates.append(_candidate(repo_id, ordered[0].path, base, ordered))

    candidates.sort(key=lambda c: (c["size_bytes"] is None, c["size_bytes"] or 0))
    return candidates


def _candidate(repo_id: str, primary: str, display: str, files: list[RepoFile]) -> dict:
    sizes = [f.size for f in files]
    total = sum(sizes) if all(s is not None for s in sizes) else None
    return {
        "filename": primary,
        "display_name": display,
        "quantization": guess_quantization(primary),
        "size_bytes": total,
        "parts": len(files),
        "verifiable": all(f.sha256 for f in files),
        "files": [{"path": f.path, "size": f.size, "sha256": f.sha256} for f in files],
    }


def find_candidate(repo_id: str, filename: str) -> dict:
    """Re-derives the candidate from Hugging Face rather than trusting
    file names/sizes/hashes sent back by the browser."""
    for c in list_gguf_candidates(repo_id):
        if c["filename"] == filename:
            return c
    raise DiscoveryError("این فایل توی repo پیدا نشد (شاید تغییر کرده) — لیست رو دوباره بارگذاری کن.", "not_found")


# ---------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------

ACTIVE_STATES = ("queued", "downloading")


@dataclass
class DownloadJob:
    id: str
    repo_id: str
    primary_filename: str
    files: list[RepoFile]
    dest_dir: Path
    status: str = "queued"  # queued | downloading | done | error | cancelled
    downloaded_bytes: int = 0
    total_bytes: Optional[int] = None
    current_file: Optional[str] = None
    note: Optional[str] = None
    speed_bps: float = 0.0
    error: Optional[str] = None
    local_path: Optional[str] = None
    result: dict = field(default_factory=dict)
    # path -> how that file was checked: "hash" (SHA-256 matched Hugging Face's),
    # "size" (only the byte count matched), "none" (nothing to compare against).
    evidence: dict = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "repo_id": self.repo_id,
            "filename": self.primary_filename,
            "status": self.status,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "current_file": self.current_file,
            "note": self.note,
            "speed_bps": self.speed_bps,
            "error": self.error,
            "local_path": self.local_path,
            "result": self.result,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class _Cancelled(Exception):
    pass


class DownloadManager:
    def __init__(self) -> None:
        self._jobs: dict[str, DownloadJob] = {}
        self._lock = threading.Lock()

    # -- public API --

    def start(
        self,
        repo_id: str,
        candidate: dict,
        dest_root: Path,
        on_complete: Optional[Callable[[DownloadJob], Optional[dict]]] = None,
    ) -> DownloadJob:
        primary = candidate["filename"]
        with self._lock:
            for j in self._jobs.values():
                if j.repo_id == repo_id and j.primary_filename == primary and j.status in ACTIVE_STATES:
                    return j  # already running: hand back the same job, don't double-download

            files = [RepoFile(f["path"], f["size"], f["sha256"]) for f in candidate["files"]]
            sizes = [f.size for f in files]
            job = DownloadJob(
                id=uuid.uuid4().hex[:12],
                repo_id=repo_id,
                primary_filename=primary,
                files=files,
                dest_dir=Path(dest_root) / repo_id.split("/")[0] / repo_id.split("/")[1],
                total_bytes=sum(sizes) if all(s is not None for s in sizes) else None,
            )
            self._jobs[job.id] = job

        threading.Thread(target=self._run, args=(job, on_complete), daemon=True, name=f"dl-{job.id}").start()
        return job

    def get(self, job_id: str) -> Optional[DownloadJob]:
        return self._jobs.get(job_id)

    def list(self) -> list[DownloadJob]:
        return sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status not in ACTIVE_STATES:
            return False
        job.cancel_event.set()
        return True

    # -- worker --

    def _run(self, job: DownloadJob, on_complete) -> None:
        try:
            job.dest_dir.mkdir(parents=True, exist_ok=True)
            self._check_disk_space(job)
            job.status = "downloading"

            for rf in job.files:
                final = job.dest_dir / Path(rf.path).name
                job.current_file = final.name
                self._download_file(job, rf, final)

            job.local_path = str(job.dest_dir / Path(job.files[0].path).name)
            job.current_file = None
            job.speed_bps = 0.0

            # Register BEFORE flipping to "done": a client polling for "done"
            # must find the model already in the catalog, not race the
            # callback and see an empty result.
            if on_complete is not None:
                job.note = "ثبت توی کاتالوگ مدل‌ها…"
                try:
                    job.result = on_complete(job) or {}
                except Exception as exc:  # registering failed; the file itself is fine
                    job.result = {"registered": False, "register_error": str(exc)}
                job.note = None

            job.status = "done"
            job.finished_at = time.time()
        except _Cancelled:
            job.status = "cancelled"
            job.finished_at = time.time()
        except DiscoveryError as exc:
            job.status, job.error, job.finished_at = "error", str(exc), time.time()
        except OSError as exc:
            job.status, job.error, job.finished_at = "error", f"خطای دیسک: {exc.strerror or exc}", time.time()
        except Exception as exc:  # last resort: a thread must never die silently
            job.status, job.error, job.finished_at = "error", f"خطای پیش‌بینی‌نشده: {exc}", time.time()

    def _check_disk_space(self, job: DownloadJob) -> None:
        if job.total_bytes is None:
            return
        already = 0
        for rf in job.files:
            final = job.dest_dir / Path(rf.path).name
            for candidate in (final, final.with_name(final.name + ".part")):
                if candidate.exists():
                    already += candidate.stat().st_size
                    break
        needed = max(job.total_bytes - already, 0) + DISK_HEADROOM_BYTES
        free = shutil.disk_usage(job.dest_dir).free
        if free < needed:
            raise DiscoveryError(
                f"فضای دیسک کافی نیست: حدود {needed / 1e9:.1f} GB لازمه ولی فقط {free / 1e9:.1f} GB آزاده."
            )

    @staticmethod
    def _hash_file(path: Path, job: DownloadJob) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                if job.cancel_event.is_set():
                    raise _Cancelled()
                block = f.read(CHUNK_BYTES)
                if not block:
                    break
                hasher.update(block)
        return hasher.hexdigest()

    def _download_file(self, job: DownloadJob, rf: RepoFile, final: Path) -> None:
        # Already on disk with the right size. Size alone proves nothing: a corrupt or
        # swapped file of the same length would be accepted. When Hugging Face publishes
        # a hash, check it; a file that doesn't match is deleted and fetched again.
        if final.exists() and rf.size is not None and final.stat().st_size == rf.size:
            if rf.sha256:
                job.note = "چک کردن فایل موجود…"
                digest = self._hash_file(final, job)
                job.note = None
                if digest.lower() == rf.sha256.lower():
                    job.downloaded_bytes += rf.size
                    job.evidence[rf.path] = "hash"
                    return
                final.unlink()  # same size, different content: never keep it
            else:
                job.downloaded_bytes += rf.size
                job.evidence[rf.path] = "size"
                return

        part = final.with_name(final.name + ".part")
        offset = part.stat().st_size if part.exists() else 0
        if rf.size is not None and offset > rf.size:
            part.unlink()
            offset = 0

        hasher = hashlib.sha256() if rf.sha256 else None
        if hasher is not None and offset:
            job.note = "چک کردن بخش قبلاً دانلودشده…"
            with open(part, "rb") as f:
                while True:
                    if job.cancel_event.is_set():
                        raise _Cancelled()
                    block = f.read(CHUNK_BYTES)
                    if not block:
                        break
                    hasher.update(block)
            job.note = None

        url = f"https://{HF_HOST}/{quote(job.repo_id, safe='/')}/resolve/main/{quote(rf.path, safe='/')}"
        already_counted = offset

        if rf.size is None or offset < rf.size:
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            resp = open_url(url, allowed_hosts=HF_ALLOWED_HOSTS, headers=headers, timeout=30)
            with resp:
                mode = "ab"
                if offset and getattr(resp, "status", 200) != 206:
                    # Server ignored the Range request: start this file over.
                    offset = 0
                    already_counted = 0
                    mode = "wb"
                    hasher = hashlib.sha256() if rf.sha256 else None
                job.downloaded_bytes += already_counted if mode == "ab" else 0

                window_start, window_bytes = time.time(), 0
                try:
                    with open(part, mode) as out:
                        while True:
                            if job.cancel_event.is_set():
                                raise _Cancelled()
                            chunk = resp.read(CHUNK_BYTES)
                            if not chunk:
                                break
                            out.write(chunk)
                            if hasher is not None:
                                hasher.update(chunk)
                            job.downloaded_bytes += len(chunk)
                            window_bytes += len(chunk)
                            now = time.time()
                            if now - window_start >= 1.0:
                                job.speed_bps = window_bytes / (now - window_start)
                                window_start, window_bytes = now, 0
                except (http.client.IncompleteRead, ConnectionError, TimeoutError) as exc:
                    raise DiscoveryError(
                        "اتصال وسط دانلود قطع شد — دوباره بزن، از همین‌جا ادامه می‌ده.", "network"
                    ) from exc
        else:
            job.downloaded_bytes += offset  # part file is already complete; only verify+rename left

        actual = part.stat().st_size
        if rf.size is not None and actual != rf.size:
            raise DiscoveryError(f"دانلود ناقص موند ({actual} از {rf.size} بایت) — دوباره بزن تا ادامه بده.", "network")
        if hasher is not None and hasher.hexdigest().lower() != rf.sha256.lower():
            part.unlink(missing_ok=True)
            raise DiscoveryError(
                "هش SHA256 فایل با هش رسمی Hugging Face نمی‌خونه — فایل پاک شد. دوباره امتحان کن.", "error"
            )
        os.replace(part, final)
        job.evidence[rf.path] = "hash" if hasher is not None else ("size" if rf.size is not None else "none")


download_manager = DownloadManager()
