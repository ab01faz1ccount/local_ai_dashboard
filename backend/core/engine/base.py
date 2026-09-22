"""
backend/core/engine/base.py

The InferenceEngine contract. This is the ONE abstraction in the whole
codebase that lets `llama.cpp` be swapped for `vLLM`, `ollama`, etc. later
without touching the API layer, the WebSocket push, or the dashboard.

Rule: nothing outside `core/engine/` may import `LlamaCppEngine` (or any
other concrete engine) directly. Everything else depends on
`InferenceEngine` only, and gets a concrete instance from a small factory
(added alongside LlamaCppEngine in the next step).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RuntimeState(str, Enum):
    """Mirrors the `status` CHECK constraint on the `runtimes` table in
    schema.sql — kept as a separate enum (not imported from storage/db.py)
    so the engine layer has zero dependency on the storage layer."""

    OFFLINE = "OFFLINE"
    STARTING = "STARTING"
    ONLINE = "ONLINE"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


@dataclass
class EngineStatus:
    state: RuntimeState
    pid: Optional[int] = None
    endpoint: Optional[str] = None          # e.g. "http://127.0.0.1:8080"
    uptime_seconds: Optional[float] = None
    error_message: Optional[str] = None


@dataclass
class EngineMetrics:
    """Engine-reported (not hardware) metrics — tokens/sec, latency, etc.
    Hardware metrics (CPU/RAM/GPU) come from PlatformProvider, not here."""

    tokens_per_sec: Optional[float] = None
    last_latency_ms: Optional[float] = None
    active_slots: Optional[int] = None
    total_requests: Optional[int] = None
    # engine-specific fields that don't deserve a first-class column
    # (e.g. llama.cpp's per-slot breakdown) go here rather than growing
    # this dataclass forever.
    extra: dict = field(default_factory=dict)


class InferenceEngine(ABC):
    """Abstract contract every local inference engine backend must satisfy.

    Implementations are responsible for:
      - process lifecycle (start/stop/restart)
      - translating their own health/metrics format into EngineStatus /
        EngineMetrics
      - isolating their own crashes: a crash in the underlying process
        must surface as EngineStatus(state=ERROR, error_message=...),
        never as an unhandled exception that could take down the control
        center itself.
    """

    @abstractmethod
    def start(self, config: dict) -> EngineStatus:
        """Launch the engine process with the given config (e.g. model
        path, ctx_size, n_gpu_layers, threads, batch_size, ...).

        Must be idempotent-ish: calling start() while already ONLINE
        should return the current status rather than spawning a second
        process.
        """
        raise NotImplementedError

    @abstractmethod
    def stop(self, timeout: float = 10.0) -> EngineStatus:
        """Gracefully stop the process (e.g. SIGTERM), escalating to a
        force-kill (SIGKILL) if it hasn't exited within `timeout` seconds.
        """
        raise NotImplementedError

    @abstractmethod
    def restart(self, config: Optional[dict] = None) -> EngineStatus:
        """Stop then start. If `config` is given, apply it before starting
        (e.g. user changed ctx_size and wants it to take effect)."""
        raise NotImplementedError

    @abstractmethod
    def get_status(self) -> EngineStatus:
        """Current lifecycle state. Should be cheap enough to poll every
        1-2s from the WebSocket loop."""
        raise NotImplementedError

    @abstractmethod
    def get_metrics(self) -> EngineMetrics:
        """Engine-reported performance metrics for the currently loaded
        model, if any."""
        raise NotImplementedError

    @abstractmethod
    def tail_logs(self, n: int = 200) -> list[str]:
        """Last `n` lines from an in-memory ring buffer of process
        stdout/stderr. No log DB at this scope — see Future Roadmap."""
        raise NotImplementedError
