"""
backend/core/hardware_fit.py

Hardware Fit Analyzer (master build prompt section 12): turns the raw
memory estimate in core/comparison.py (`estimate_system_impact`) into a
verdict a person can act on -- GOOD / WARNING / LIKELY_TO_EXCEED /
NOT_RECOMMENDED -- against THIS machine's actual hardware.

The estimate is a heuristic (file size + a generic KV-cache term), so
the thresholds deliberately leave headroom: a model whose estimate
already uses 95% of memory is far more likely to run out for real than
the number alone suggests, hence a separate LIKELY_TO_EXCEED band
between "tight" and "definitely won't fit".

Two places a model can live, judged independently, best one wins:
  - GPU: compared against total VRAM (only when a GPU is available).
  - CPU: compared against total RAM minus a reserve for the OS and the
    control center itself -- RAM is never fully available to a model.
A model that fits in neither is NOT_RECOMMENDED. A model that fits RAM but
not VRAM is reported against RAM with `target="cpu"`, since llama.cpp will
still run it there (just slower) -- the reason text says so.
"""

from __future__ import annotations

from typing import Optional

GOOD = "GOOD"
WARNING = "WARNING"
LIKELY_TO_EXCEED = "LIKELY_TO_EXCEED"
NOT_RECOMMENDED = "NOT_RECOMMENDED"
UNKNOWN = "UNKNOWN"

# (upper bound of estimate/capacity ratio, label), checked in order.
_BANDS = ((0.70, GOOD), (0.90, WARNING), (1.00, LIKELY_TO_EXCEED))

_RAM_RESERVE_MB = 2048.0
_RAM_RESERVE_FRACTION = 0.20  # on small machines 2 GB would be most of the RAM


def _label_for_ratio(ratio: float) -> str:
    for upper, label in _BANDS:
        if ratio <= upper:
            return label
    return NOT_RECOMMENDED


_RANK = {GOOD: 0, WARNING: 1, LIKELY_TO_EXCEED: 2, NOT_RECOMMENDED: 3}


def ram_capacity_mb(total_mb: float) -> float:
    """RAM a model can realistically use: total minus an OS/app reserve
    (the smaller of a fixed 2 GB and 20% of total)."""
    reserve = min(_RAM_RESERVE_MB, total_mb * _RAM_RESERVE_FRACTION)
    return max(total_mb - reserve, 0.0)


def assess_fit(estimated_mb: float, *, ram_total_mb: Optional[float], gpu_available: bool, vram_total_mb: Optional[float]) -> dict:
    """Pure function: no I/O, so every threshold edge is testable."""
    if not ram_total_mb or ram_total_mb <= 0:
        return {
            "label": UNKNOWN, "target": None, "ratio": None, "capacity_mb": None, "estimated_mb": round(estimated_mb, 1),
            "reason": "This machine's memory couldn't be read, so fit can't be judged.",
        }

    ram_cap = ram_capacity_mb(ram_total_mb)
    cpu_ratio = estimated_mb / ram_cap if ram_cap > 0 else float("inf")
    cpu = {"target": "cpu", "capacity_mb": round(ram_cap, 1), "ratio": cpu_ratio, "label": _label_for_ratio(cpu_ratio)}

    candidates = [cpu]
    if gpu_available and vram_total_mb and vram_total_mb > 0:
        gpu_ratio = estimated_mb / vram_total_mb
        candidates.append(
            {"target": "gpu", "capacity_mb": round(vram_total_mb, 1), "ratio": gpu_ratio, "label": _label_for_ratio(gpu_ratio)}
        )

    # Best verdict wins; on a tie prefer the GPU (faster) when there is one.
    best = min(candidates, key=lambda c: (_RANK[c["label"]], 0 if c["target"] == "gpu" else 1))
    pct = round(best["ratio"] * 100) if best["ratio"] != float("inf") else None
    where = "VRAM" if best["target"] == "gpu" else "RAM"

    if best["label"] == NOT_RECOMMENDED:
        reason = f"Estimated {estimated_mb:.0f} MB exceeds the {best['capacity_mb']:.0f} MB available in {where}"
        reason += " (and in VRAM)." if best["target"] == "cpu" and gpu_available else "."
    else:
        reason = f"Estimated to use {pct}% of {where} ({estimated_mb:.0f} of {best['capacity_mb']:.0f} MB)."
        if best["target"] == "cpu" and gpu_available:
            reason += " It won't fit entirely in VRAM, so expect CPU or partial-offload speed."
        if best["label"] == LIKELY_TO_EXCEED:
            reason += " The estimate is rough; this is close enough to the limit that real use will probably go over."

    return {
        "label": best["label"],
        "target": best["target"],
        "ratio": round(best["ratio"], 3) if best["ratio"] != float("inf") else None,
        "capacity_mb": best["capacity_mb"],
        "estimated_mb": round(estimated_mb, 1),
        "reason": reason,
    }


def assess_from_snapshot(estimated_mb: float, hardware: Optional[dict]) -> dict:
    """`hardware` is runtime_manager.get_hardware_snapshot()'s dict
    ({"cpu","memory","gpu"}); None (or a malformed one) -> UNKNOWN rather
    than an exception, since a fit badge is decoration on a table that
    must still render."""
    try:
        mem, gpu = hardware["memory"], hardware["gpu"]
        return assess_fit(estimated_mb, ram_total_mb=mem.total_mb, gpu_available=bool(gpu.available), vram_total_mb=gpu.vram_total_mb)
    except (TypeError, KeyError, AttributeError):
        return assess_fit(estimated_mb, ram_total_mb=None, gpu_available=False, vram_total_mb=None)
