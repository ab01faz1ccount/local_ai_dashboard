"""
backend/core/comparison.py

Model Comparison feature. Combines four kinds of data for each model, per
the scope the user confirmed (all three, plus the metadata that was
already sitting in the DB):

  1. Metadata already on the `models` row (size, quantization, param
     count, context length) -- free, always available.
  2. An estimated system-impact figure (RAM/VRAM a runtime of this model
     would likely need) -- computed locally, heuristic, never exact.
  3. Public benchmark scores pulled from the model's Hugging Face card, if
     the model has been linked to one (see `update_model_hf_link`).
  4. Real-world performance actually observed by this app (avg tokens/sec,
     avg latency, request count), pulled from `request_logs` via
     `storage.db.get_model_performance_stats`.

None of this is exact. Every returned number that's an estimate rather
than an observed fact says so in its own field name (`estimated_*`) so
the frontend can label it instead of presenting it as measured truth.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from sqlalchemy.orm import Session

from ..storage import db as storage_db
from ..storage.db import MLModel

HF_API_TIMEOUT_SECONDS = 8
HF_MODEL_INFO_URL = "https://huggingface.co/api/models/{repo_id}"

# Rough bytes-per-parameter by quantization family, used only when
# param_count is known and file_size_bytes isn't a reliable stand-in
# (it always is here, but this stays as a documented fallback path).
_BYTES_PER_PARAM_BY_QUANT = {
    "q2": 0.35, "q3": 0.45, "q4": 0.55, "q5": 0.65, "q6": 0.75, "q8": 1.0,
    "f16": 2.0, "fp16": 2.0, "f32": 4.0, "fp32": 4.0,
}


def _quant_family(quantization: Optional[str]) -> Optional[str]:
    if not quantization:
        return None
    q = quantization.lower()
    for key in _BYTES_PER_PARAM_BY_QUANT:
        if key in q:
            return key
    return None


def estimate_system_impact(model: MLModel) -> dict:
    """Heuristic estimate of what running this model actually costs.

    Base RAM/VRAM need is approximated as the file size itself (a GGUF's
    on-disk size is close to its resident weight size for a given quant),
    plus a KV-cache overhead that scales with context_length -- the part
    that file size alone doesn't capture. The KV-cache term uses a
    generic per-token-per-1k-context byte estimate rather than the
    model's true hidden-dim/layer count (not stored), so it is
    deliberately a coarse, labeled estimate, not a promise.
    """
    base_mb = model.file_size_bytes / (1024 * 1024)

    # ~ generic small-to-mid dense model KV-cache footprint per 1K context
    # tokens at fp16 KV cache; deliberately conservative/generic since we
    # don't have per-model architecture details (layers, heads, dim).
    kv_cache_mb_per_1k_context = 80.0
    context = model.context_length or 4096
    estimated_kv_cache_mb = (context / 1000.0) * kv_cache_mb_per_1k_context

    estimated_total_mb = base_mb + estimated_kv_cache_mb

    return {
        "estimated_base_mb": round(base_mb, 1),
        "estimated_kv_cache_mb": round(estimated_kv_cache_mb, 1),
        "estimated_total_mb": round(estimated_total_mb, 1),
        "note": (
            "Rough estimate from file size + a generic KV-cache-per-context "
            "heuristic. Actual RAM/VRAM use depends on the model's real "
            "architecture and runtime flags; treat this as a ballpark, not "
            "a guarantee."
        ),
    }


def _fetch_json(url: str) -> Optional[dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "local-ai-control-center"})
    try:
        with urllib.request.urlopen(req, timeout=HF_API_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


def fetch_benchmark_data(hf_repo_id: str) -> Optional[dict]:
    """Pulls whatever public eval-results block Hugging Face exposes for
    this repo's model card (the `model-index` section of its README
    frontmatter, surfaced by the HF API as `cardData.model-index`).

    Not every repo publishes this -- many don't -- so a `None`/empty
    result is the normal case for a lot of GGUF quant repos (the
    original base model's card, if linked, is usually the one that has
    it). Returns None on any network failure rather than raising, since
    this is a "nice to have" enrichment, not a required field.
    """
    data = _fetch_json(HF_MODEL_INFO_URL.format(repo_id=hf_repo_id))
    if data is None:
        return None

    card_data = data.get("cardData") or {}
    model_index = card_data.get("model-index")
    results = []
    if isinstance(model_index, list):
        for entry in model_index:
            for res in entry.get("results", []) or []:
                dataset = (res.get("dataset") or {}).get("name")
                for metric in res.get("metrics", []) or []:
                    results.append(
                        {
                            "task": (res.get("task") or {}).get("type"),
                            "dataset": dataset,
                            "metric_name": metric.get("name") or metric.get("type"),
                            "value": metric.get("value"),
                        }
                    )

    return {
        "hf_repo_id": hf_repo_id,
        "likes": data.get("likes"),
        "downloads": data.get("downloads"),
        "tags": data.get("tags"),
        "pipeline_tag": data.get("pipeline_tag"),
        "eval_results": results,  # [] if the card publishes none
    }


def _metadata_dict(model: MLModel) -> dict:
    return {
        "id": model.id,
        "name": model.name,
        "file_size_bytes": model.file_size_bytes,
        "quantization": model.quantization,
        "param_count": model.param_count,
        "context_length": model.context_length,
        "hf_repo_id": model.hf_repo_id,
    }


def compare_models(db: Session, model_ids: list[int]) -> dict:
    """Builds one comparison row per requested model, in the order given.
    Skips ids that don't exist rather than raising, so a stale id in a
    saved comparison view doesn't break the whole comparison.

    Returns `{"internet_available": bool, "rows": [...]}` rather than a
    bare list: three of the four data sources (metadata, estimated
    system impact, real-world performance) are fully local and always
    work; only `public_benchmarks` needs the internet, and the caller
    needs to know *up front* whether a `None` there means "offline" or
    "this repo just doesn't publish eval results" -- checking
    connectivity once per call (instead of once per model) also avoids
    piling up redundant timeouts when it's already known to be offline.
    """
    from ..core import connectivity

    internet_available = connectivity.check_internet()
    models = storage_db.get_models_by_ids(db, model_ids)

    rows = []
    for model in models:
        benchmarks = None
        if model.hf_repo_id and internet_available:
            benchmarks = fetch_benchmark_data(model.hf_repo_id)
        row = {
            "metadata": _metadata_dict(model),
            "estimated_system_impact": estimate_system_impact(model),
            "real_world_performance": storage_db.get_model_performance_stats(db, model.id),
            "public_benchmarks": benchmarks,
        }
        rows.append(row)
    return {"internet_available": internet_available, "rows": rows}
