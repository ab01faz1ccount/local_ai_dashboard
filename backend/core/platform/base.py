"""
backend/core/platform/base.py

The PlatformProvider contract. This is the ONE abstraction that keeps
`if platform.system() == "Windows"` checks out of the rest of the codebase.
`windows/provider.py` and `linux/provider.py` each hold exactly one
concrete implementation; everything else calls `get_platform_provider()`
and talks to the interface only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class CpuMetrics:
    percent: float
    core_count: int
    per_core_percent: Optional[list[float]] = None


@dataclass
class MemoryMetrics:
    used_mb: float
    total_mb: float
    percent: float


@dataclass
class GpuMetrics:
    """`available=False` is the graceful "no GPU / unsupported vendor"
    case required by the spec — providers must return this, never raise,
    when no NVIDIA GPU is found."""

    available: bool
    name: Optional[str] = None
    utilization_percent: Optional[float] = None
    vram_used_mb: Optional[float] = None
    vram_total_mb: Optional[float] = None


class PlatformProvider(ABC):
    @abstractmethod
    def get_cpu_metrics(self) -> CpuMetrics:
        raise NotImplementedError

    @abstractmethod
    def get_memory_metrics(self) -> MemoryMetrics:
        raise NotImplementedError

    @abstractmethod
    def get_gpu_metrics(self) -> GpuMetrics:
        """MVP scope is NVIDIA-only (via pynvml / nvidia-smi fallback).
        Must return GpuMetrics(available=False) — never raise — when no
        supported GPU is present."""
        raise NotImplementedError

    @abstractmethod
    def start_process(self, executable_path: str, args: list[str], cwd: Optional[str] = None) -> int:
        """Launch a subprocess (e.g. llama-server) and return its PID.
        A crash in the child process must not propagate as an exception
        that takes down the caller — the engine layer polls status
        separately."""
        raise NotImplementedError

    @abstractmethod
    def stop_process(self, pid: int, timeout: float = 10.0) -> bool:
        """Terminate gracefully first, force-kill after `timeout` seconds.
        Returns True once the process is confirmed stopped."""
        raise NotImplementedError

    @abstractmethod
    def is_process_running(self, pid: int) -> bool:
        """Cheap liveness check. Lets InferenceEngine detect a crash (the
        process died on its own between polls) without needing a raw
        subprocess handle -- that handle stays inside the provider."""
        raise NotImplementedError

    @abstractmethod
    def get_process_logs(self, pid: int, n: int = 200) -> list[str]:
        """Last `n` lines captured from the process's stdout/stderr since
        it was started via start_process(). Backs InferenceEngine.tail_logs()."""
        raise NotImplementedError


def get_platform_provider() -> "PlatformProvider":
    """Factory: returns the right concrete provider for the current OS.
    This is the single place in the codebase allowed to branch on
    platform.system()."""
    import platform as _platform

    system = _platform.system()
    if system == "Windows":
        from .windows.provider import WindowsPlatformProvider

        return WindowsPlatformProvider()
    elif system == "Linux":
        from .linux.provider import LinuxPlatformProvider

        return LinuxPlatformProvider()
    else:
        raise NotImplementedError(f"Unsupported platform: {system}")
