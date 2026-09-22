"""
backend/core/authenticity.py

Model Authenticity Verification (opt-in). Per the user's confirmed scope,
this checks *both* of the following, not just one:

  1. Exact file hash (SHA256) against the official hash Hugging Face
     publishes for that exact repo/file.
  2. Metadata (file size) as a fallback when an exact hash isn't
     available -- e.g. the file isn't served as a Git-LFS object, which
     is the only case Hugging Face's CDN exposes a content hash for via
     a cheap HEAD request.

Nothing here is automatic or silent: verification only runs when the
user explicitly links a model to a Hugging Face repo/file
(`update_model_hf_link`) and explicitly triggers a check
(`POST /models/{id}/verify`), per the "opt-in" requirement from the
original roadmap. Hashing a multi-GB GGUF file takes real time, so the
API layer runs this as a background task rather than blocking the
request -- see `api/http.py`.
"""

from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from typing import Optional

from ..storage.db import MLModel

HF_TIMEOUT_SECONDS = 10
HASH_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB, keeps memory flat on multi-GB files

VerificationStatus = str  # one of the values below, kept as plain str (see db.py's CHECK constraint)
STATUS_VERIFIED = "verified"
STATUS_HASH_MISMATCH = "hash_mismatch"
STATUS_METADATA_ONLY = "metadata_only"
STATUS_METADATA_MISMATCH = "metadata_mismatch"
STATUS_NOT_FOUND = "not_found"
STATUS_NO_HASH_AVAILABLE = "no_hash_available"
STATUS_ERROR = "error"


def compute_file_sha256(path: str) -> str:
    """Streams the file in fixed-size chunks rather than reading it whole,
    since GGUF models are commonly several GB -- this must not require
    loading the entire file into memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_hf_file_head(hf_repo_id: str, filename: str) -> Optional[dict]:
    """HEAD request against the file's resolve URL. For a Git-LFS-tracked
    file (true of essentially every GGUF on the Hub, since they're all
    multi-hundred-MB+), Hugging Face's CDN returns the object's SHA256 in
    the `X-Linked-Etag` header and its true size in `X-Linked-Size` --
    this is the standard way to get an authoritative content hash without
    downloading the file. Returns None on 404 or any network failure.
    """
    url = f"https://huggingface.co/{hf_repo_id}/resolve/main/{filename}"
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "local-ai-control-center"})
    try:
        with urllib.request.urlopen(req, timeout=HF_TIMEOUT_SECONDS) as resp:
            headers = resp.headers
            etag = headers.get("X-Linked-Etag")
            size = headers.get("X-Linked-Size") or headers.get("Content-Length")
            return {
                "sha256": etag.strip('"') if etag else None,
                "size_bytes": int(size) if size else None,
            }
    except urllib.error.HTTPError:
        return None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def verify_model_authenticity(model: MLModel) -> dict:
    """Runs the full check for one model and returns a result dict meant
    to be stored via `storage.db.update_model_verification` as:
      - verification_status: result["status"]
      - verification_details_json: result (the whole dict)
    """
    if not model.hf_repo_id or not model.hf_filename:
        return {
            "status": STATUS_NOT_FOUND,
            "reason": "model isn't linked to a Hugging Face repo/file yet",
            "local_sha256": None,
            "remote_sha256": None,
            "local_size_bytes": model.file_size_bytes,
            "remote_size_bytes": None,
        }

    remote = _fetch_hf_file_head(model.hf_repo_id, model.hf_filename)
    if remote is None:
        return {
            "status": STATUS_NOT_FOUND,
            "reason": f"{model.hf_repo_id}/{model.hf_filename} not found on Hugging Face (or unreachable)",
            "local_sha256": None,
            "remote_sha256": None,
            "local_size_bytes": model.file_size_bytes,
            "remote_size_bytes": None,
        }

    try:
        local_sha256 = compute_file_sha256(model.file_path)
    except OSError as exc:
        return {
            "status": STATUS_ERROR,
            "reason": f"couldn't read local file: {exc}",
            "local_sha256": None,
            "remote_sha256": remote.get("sha256"),
            "local_size_bytes": model.file_size_bytes,
            "remote_size_bytes": remote.get("size_bytes"),
        }

    remote_sha256 = remote.get("sha256")
    remote_size = remote.get("size_bytes")

    if remote_sha256:
        status = STATUS_VERIFIED if local_sha256 == remote_sha256 else STATUS_HASH_MISMATCH
        reason = (
            "local file hash matches Hugging Face's published hash"
            if status == STATUS_VERIFIED
            else "local file hash does NOT match Hugging Face's published hash for this file"
        )
    elif remote_size is not None:
        status = STATUS_METADATA_ONLY if remote_size == model.file_size_bytes else STATUS_METADATA_MISMATCH
        reason = (
            "no content hash available from Hugging Face for this file; "
            "file size matches as a weaker fallback check"
            if status == STATUS_METADATA_ONLY
            else "no content hash available, and file size does NOT match Hugging Face's copy"
        )
    else:
        status = STATUS_NO_HASH_AVAILABLE
        reason = "Hugging Face returned neither a hash nor a size for this file"

    return {
        "status": status,
        "reason": reason,
        "local_sha256": local_sha256,
        "remote_sha256": remote_sha256,
        "local_size_bytes": model.file_size_bytes,
        "remote_size_bytes": remote_size,
    }
