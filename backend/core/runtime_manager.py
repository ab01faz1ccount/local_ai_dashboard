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

from . import events
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
        self._emit_for_outcome(runtime, status, phase="start")
        return status

    def stop(self, db: Session, runtime: Runtime, timeout: float = 10.0) -> EngineStatus:
        engine = self._get_or_create_engine(runtime)
        status = engine.stop(timeout=timeout)
        self._sync_db(db, runtime, status)
        self._emit_for_outcome(runtime, status, phase="stop")
        return status

    def restart(self, db: Session, runtime: Runtime) -> EngineStatus:
        engine = self._get_or_create_engine(runtime)
        config = {**runtime.config_json, "host": runtime.host, "port": runtime.port}
        if runtime.model and runtime.model.file_path:
            config.setdefault("model_path", runtime.model.file_path)
        status = engine.restart(config)
        self._sync_db(db, runtime, status)
        if status.state == RuntimeState.ONLINE:
            # A restart really is "it stopped, then it started again" --
            # emitting both (rather than inventing a runtime.restarted
            # type) keeps every consumer of the event log working from
            # the same small, spec-defined vocabulary.
            events.emit(events.EventType.RUNTIME_STOPPED, runtime_id=runtime.id, metadata={"reason": "restart"})
            self._emit_started(runtime, status, extra_metadata={"reason": "restart"})
        elif status.state == RuntimeState.ERROR:
            events.emit(
                events.EventType.RUNTIME_CRASHED,
                runtime_id=runtime.id,
                metadata={"phase": "restart", "error": status.error_message},
            )
        return status

    def get_status(self, db: Session, runtime: Runtime) -> EngineStatus:
        # Snapshot the DB's last-known state BEFORE _sync_db overwrites it,
        # so a poll (this is what the /ws/metrics loop calls every ~1.5s)
        # can tell "still ONLINE" apart from "was ONLINE, just died" --
        # the actual crash-detection case start()/stop()/restart() above
        # can't see, since nothing called them for this transition.
        previous_state = runtime.status
        engine = self._get_or_create_engine(runtime)
        status = engine.get_status()
        self._sync_db(db, runtime, status)

        if previous_state == RuntimeState.ONLINE.value and status.state == RuntimeState.ERROR:
            events.emit(
                events.EventType.RUNTIME_CRASHED,
                runtime_id=runtime.id,
                metadata={"phase": "poll", "error": status.error_message},
            )
        elif previous_state == RuntimeState.ONLINE.value and status.state == RuntimeState.OFFLINE:
            # The process ended without going through this manager's own
            # stop() -- still worth a runtime.stopped, just flagged with
            # how it was noticed rather than claiming it was requested.
            events.emit(
                events.EventType.RUNTIME_STOPPED, runtime_id=runtime.id, metadata={"detected_via": "poll"}
            )
        return status

    def _emit_started(self, runtime: Runtime, status: EngineStatus, extra_metadata: Optional[dict] = None) -> None:
        metadata = {"endpoint": status.endpoint, **(extra_metadata or {})}
        events.emit(events.EventType.RUNTIME_STARTED, runtime_id=runtime.id, metadata=metadata)
        if runtime.model_id is not None:
            events.emit(events.EventType.MODEL_LOADED, runtime_id=runtime.id, metadata={"model_id": runtime.model_id})

    def _emit_for_outcome(self, runtime: Runtime, status: EngineStatus, *, phase: str) -> None:
        """Shared by start()/stop(): report exactly what happened, not
        just what was asked for -- a failed stop still ends in ERROR, not
        OFFLINE, and that has to show up as runtime.crashed, not silence."""
        if status.state == RuntimeState.ONLINE:
            self._emit_started(runtime, status)
        elif status.state == RuntimeState.OFFLINE:
            events.emit(events.EventType.RUNTIME_STOPPED, runtime_id=runtime.id)
            if runtime.model_id is not None:
                events.emit(
                    events.EventType.MODEL_UNLOADED, runtime_id=runtime.id, metadata={"model_id": runtime.model_id}
                )
        elif status.state == RuntimeState.ERROR:
            events.emit(
                events.EventType.RUNTIME_CRASHED,
                runtime_id=runtime.id,
                metadata={"phase": phase, "error": status.error_message},
            )

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
