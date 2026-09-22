"""
backend/core/runtime_manager.py

Glue layer between the DB (a `runtimes` row = durable config + last-known
status) and the in-memory `InferenceEngine` instances (a live process
handle that only exists while this Python process is running).

This is the ONE place that:
  - creates a LlamaCppEngine per runtime_id (keyed in memory)
  - mirrors engine state back onto the DB row after every action, so a
    GET /api/v1/runtimes always reflects reality even between WS pushes
  - is what backend/api/http.py and backend/api/ws.py both call into,
    instead of touching InferenceEngine or the DB directly.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from .engine.base import EngineMetrics, EngineStatus, InferenceEngine, RuntimeState
from .engine.llama_cpp_engine import LlamaCppEngine
from .platform.base import PlatformProvider, get_platform_provider
from .. import storage  # noqa: F401  (kept for clarity of package relation)
from ..storage.db import Runtime


class RuntimeManager:
    def __init__(self, platform_provider: Optional[PlatformProvider] = None):
        self._platform = platform_provider or get_platform_provider()
        self._engines: dict[int, InferenceEngine] = {}

    def _get_or_create_engine(self, runtime: Runtime) -> InferenceEngine:
        engine = self._engines.get(runtime.id)
        if engine is None:
            engine = LlamaCppEngine(self._platform, runtime.executable_path)
            self._engines[runtime.id] = engine
        return engine

    def _sync_db(self, db: Session, runtime: Runtime, status: EngineStatus) -> None:
        runtime.status = status.state.value
        runtime.pid = status.pid
        runtime.last_error = status.error_message
        db.commit()

    # -- actions used by the API layer -----------------------------------

    def start(self, db: Session, runtime: Runtime) -> EngineStatus:
        engine = self._get_or_create_engine(runtime)
        config = {**runtime.config_json, "host": runtime.host, "port": runtime.port}
        if runtime.model and runtime.model.file_path:
            config.setdefault("model_path", runtime.model.file_path)
        status = engine.start(config)
        self._sync_db(db, runtime, status)
        return status

    def stop(self, db: Session, runtime: Runtime, timeout: float = 10.0) -> EngineStatus:
        engine = self._get_or_create_engine(runtime)
        status = engine.stop(timeout=timeout)
        self._sync_db(db, runtime, status)
        return status

    def restart(self, db: Session, runtime: Runtime) -> EngineStatus:
        engine = self._get_or_create_engine(runtime)
        config = {**runtime.config_json, "host": runtime.host, "port": runtime.port}
        if runtime.model and runtime.model.file_path:
            config.setdefault("model_path", runtime.model.file_path)
        status = engine.restart(config)
        self._sync_db(db, runtime, status)
        return status

    def get_status(self, db: Session, runtime: Runtime) -> EngineStatus:
        engine = self._get_or_create_engine(runtime)
        status = engine.get_status()
        self._sync_db(db, runtime, status)
        return status

    def get_metrics(self, runtime: Runtime) -> EngineMetrics:
        engine = self._get_or_create_engine(runtime)
        return engine.get_metrics()

    def get_logs(self, runtime: Runtime, n: int = 200) -> list[str]:
        engine = self._get_or_create_engine(runtime)
        return engine.tail_logs(n)

    def get_hardware_snapshot(self) -> dict:
        """System-wide (not per-runtime) CPU/RAM/GPU, for the dashboard's
        always-visible hardware panel."""
        cpu = self._platform.get_cpu_metrics()
        mem = self._platform.get_memory_metrics()
        gpu = self._platform.get_gpu_metrics()
        return {"cpu": cpu, "memory": mem, "gpu": gpu}


# Single process-wide instance -- there is only ever one control center
# process managing these engines, so a module-level singleton (imported by
# both http.py and ws.py) is simpler and safer here than trying to thread
# an instance through FastAPI's dependency system for something stateful.
runtime_manager = RuntimeManager()
