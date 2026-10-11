"""
backend/core/model_recommend.py

"Will it run here, and how should I start it?" -- for ANY GGUF file, judged
before it is even downloaded. Pure functions, no I/O: the caller hands in the
file sizes (from the Hugging Face file list) and this machine's hardware
snapshot (runtime_manager.get_hardware_snapshot()).

Everything is generic on purpose. It reasons only about bytes and memory --
never about a particular model family or agent -- and it reuses the same
heuristic and verdict bands as the local catalog (core/hardware_fit.py), so a
file looks the same before and after it is downloaded.

Three outputs:
  * annotate_files()   -- a hardware_fit verdict on every candidate file
  * pick_recommended() -- the single file worth downloading on this machine
  * suggest_launch_config() -- llama-server settings derived from the hardware
    (GPU offload, a context window that still fits, --jinja for tool use)
"""

from __future__ import annotations

from typing import Optional

from . import hardware_fit

# Largest first: an agent wants a long context, so we try big windows first and
# keep the biggest one that still fits comfortably.
CONTEXT_LADDER = (32768, 16384, 8192, 4096)
MIN_AGENT_CONTEXT = 8192  # below this most agent CLIs run out of room immediately

_USABLE = (hardware_fit.GOOD, hardware_fit.WARNING)


def _fit(size_bytes: float, hardware: Optional[dict], context: Optional[int]) -> dict:
    est = hardware_fit.estimate_memory_mb(size_bytes, context)
    return hardware_fit.assess_from_snapshot(est["total_mb"], hardware)


def annotate_files(files: list[dict], hardware: Optional[dict], context: Optional[int] = None) -> list[dict]:
    """Copies of `files` with a `hardware_fit` verdict added. A file whose size
    the hub didn't report gets no verdict (None) rather than a made-up one."""
    out = []
    for f in files:
        size = f.get("size_bytes")
        usable = isinstance(size, (int, float)) and size > 0
        fit = _fit(size, hardware, context) if usable else None
        gpu = hardware_fit.gpu_label(hardware_fit.estimate_memory_mb(size, context)["total_mb"], hardware) if usable else None
        out.append({**f, "hardware_fit": fit, "gpu_fit": gpu, "recommended": False})
    return out


def pick_recommended(files: list[dict]) -> Optional[str]:
    """Filename of the best file for this machine, or None.

    Speed first, then quality: a file that fits the GPU beats one that only fits
    system RAM. Within a tier the LARGEST file wins (a bigger quant of the same
    model is the higher-quality one). Tiers, best first:
      GPU comfortable -> GPU tight -> RAM comfortable -> RAM tight.
    Files that fit nowhere, or have no size, are never recommended."""
    tiers = (
        lambda f: f.get("gpu_fit") == hardware_fit.GOOD,
        lambda f: f.get("gpu_fit") == hardware_fit.WARNING,
        lambda f: (f.get("hardware_fit") or {}).get("label") == hardware_fit.GOOD,
        lambda f: (f.get("hardware_fit") or {}).get("label") == hardware_fit.WARNING,
    )
    for in_tier in tiers:
        pool = [f for f in files if in_tier(f)]
        if pool:
            return max(pool, key=lambda f: f.get("size_bytes") or 0)["filename"]
    return None


def annotate_and_recommend(files: list[dict], hardware: Optional[dict], context: Optional[int] = None) -> list[dict]:
    annotated = annotate_files(files, hardware, context)
    best = pick_recommended(annotated)
    for f in annotated:
        f["recommended"] = f["filename"] == best
    return annotated


def suggest_launch_config(
    size_bytes: float,
    hardware: Optional[dict],
    *,
    trained_context: Optional[int] = None,
    for_agent: bool = True,
) -> dict:
    """Settings that suit this machine, plus the reasons -- so the UI can show
    WHY, and the user can override any of it.

    Returns {"config": {...}, "fit": <verdict at the chosen context>, "notes": [...]}.
    Only keys llama-server flags exist for are produced (see
    core/engine/llama_cpp_engine.py::_build_args)."""
    notes: list[str] = []
    ladder = [c for c in CONTEXT_LADDER if not trained_context or c <= trained_context] or [min(CONTEXT_LADDER)]

    def gpu_at(ctx: int) -> Optional[str]:
        return hardware_fit.gpu_label(hardware_fit.estimate_memory_mb(size_bytes, ctx)["total_mb"], hardware)

    # Prefer the biggest window that fits the GPU comfortably, then tightly; with no
    # GPU (or too big for it) judge against the overall verdict the same way.
    chosen_ctx = ladder[-1]
    on_gpu = False
    for wanted in (hardware_fit.GOOD, hardware_fit.WARNING):
        hit = next((c for c in ladder if gpu_at(c) == wanted), None)
        if hit is not None:
            chosen_ctx, on_gpu = hit, True
            break
    if not on_gpu:
        for wanted in (hardware_fit.GOOD, hardware_fit.WARNING):
            hit = next((c for c in ladder if _fit(size_bytes, hardware, c)["label"] == wanted), None)
            if hit is not None:
                chosen_ctx = hit
                break
    chosen_fit = _fit(size_bytes, hardware, chosen_ctx)

    config: dict = {"ctx_size": chosen_ctx}
    if on_gpu:
        config["n_gpu_layers"] = 999  # "all layers": llama.cpp clamps it to the model's real count
        notes.append("The model fits in VRAM, so every layer is offloaded to the GPU.")
    elif chosen_fit["target"] == "cpu" and chosen_fit["label"] in _USABLE:
        notes.append("It does not fit in VRAM; it will run from system RAM (slower).")
    if chosen_fit["label"] not in _USABLE:
        notes.append("This model is likely too big for this machine; a smaller quantization is advised.")
    if for_agent:
        config["jinja"] = True  # llama-server only honours a tools array with the model's Jinja template
        notes.append("--jinja is on: agents need it for tool calls.")
        if chosen_ctx < MIN_AGENT_CONTEXT:
            notes.append(f"Only a {chosen_ctx}-token window fits; agent CLIs may run out of room quickly.")
    return {"config": config, "fit": chosen_fit, "notes": notes}
