"""
backend/core/platform/_common.py

Shared, genuinely OS-agnostic metric collection. `psutil` and `pynvml`
already abstract over Windows/Linux internally, so there is no reason to
duplicate this logic in both windows/provider.py and linux/provider.py --
they each import and delegate to these functions. Only *process
management* (start/stop, which really does need OS-specific handling:
POSIX signals + process groups vs Windows CREATE_NEW_PROCESS_GROUP /
CTRL_BREAK_EVENT) lives in the two provider files.
"""

from __future__ import annotations

import shutil
import subprocess

import psutil

from .base import CpuMetrics, MemoryMetrics, GpuMetrics

_pynvml_available = False
try:
    import pynvml

    pynvml.nvmlInit()
    _pynvml_available = True
except Exception:
    _pynvml_available = False


def get_cpu_metrics() -> CpuMetrics:
    per_core = psutil.cpu_percent(percpu=True)
    overall = psutil.cpu_percent()
    # cpu_percent() can read 0.0 on the very first call in a process;
    # fall back to the per-core average so callers never see a bogus zero.
    if overall == 0.0 and per_core:
        overall = sum(per_core) / len(per_core)
    return CpuMetrics(percent=overall, core_count=psutil.cpu_count() or 0, per_core_percent=per_core)


def get_memory_metrics() -> MemoryMetrics:
    vm = psutil.virtual_memory()
    used_mb = (vm.total - vm.available) / (1024 * 1024)
    total_mb = vm.total / (1024 * 1024)
    return MemoryMetrics(used_mb=used_mb, total_mb=total_mb, percent=vm.percent)


def get_gpu_metrics() -> GpuMetrics:
    """NVIDIA-only for MVP (spec). Tries pynvml first, falls back to
    parsing `nvidia-smi`, and returns available=False -- never raises --
    if neither path works (no GPU, no NVIDIA driver, AMD/Intel GPU, etc.)."""

    if _pynvml_available:
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode()
            return GpuMetrics(
                available=True,
                name=name,
                utilization_percent=float(util.gpu),
                vram_used_mb=mem.used / (1024 * 1024),
                vram_total_mb=mem.total / (1024 * 1024),
            )
        except Exception:
            pass  # fall through to nvidia-smi

    if shutil.which("nvidia-smi"):
        try:
            out = (
                subprocess.check_output(
                    [
                        "nvidia-smi",
                        "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                        "--format=csv,noheader,nounits",
                    ],
                    timeout=3,
                )
                .decode()
                .strip()
            )
            first_line = out.splitlines()[0]
            name, util_pct, mem_used, mem_total = [p.strip() for p in first_line.split(",")]
            return GpuMetrics(
                available=True,
                name=name,
                utilization_percent=float(util_pct),
                vram_used_mb=float(mem_used),
                vram_total_mb=float(mem_total),
            )
        except Exception:
            pass

    return GpuMetrics(available=False)
