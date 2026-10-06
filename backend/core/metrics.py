"""
backend/core/metrics.py

Background metrics history (completes Phase 3 / master build prompt
section 23: "Analytics" needs a time series, not just a live number).

api/ws.py's `/ws/metrics` socket already pushes a live snapshot to
whichever browser tab has it open, every ~1.5s -- but that's a per-client
view: nothing is written anywhere, so it vanishes the moment no tab is
connected, and there was never any history to chart. `MetricsRecorder`
is the server's own always-on sampler: independent of any browser,
it periodically writes one `MetricsSnapshot` row (storage/db.py) per
ONLINE runtime and emits `RUNTIME_METRICS` with the same numbers, so
a live listener (a future Notifications panel, say) sees them too
without polling the DB.

A snapshot is only written for a runtime that's ONLINE -- an offline
runtime has no tokens_per_sec/active_slots to sample, and a row of all-
None values would just be dead weight in a chart. Hardware (CPU/RAM/GPU)
is sampled once per tick (machine-wide, not per runtime) and copied onto
every online runtime's row, same as `/ws/metrics` already does.

Retention: a 15s tick forever would grow `metrics_snapshots` without
bound, so each tick prunes each runtime back down to
`retention_per_runtime` rows (oldest first) right after writing --
cheap, and keeps the table's growth bounded by the number of runtimes
rather than by how long the process has been running.
"""

from __future__ import annotations

import threading
from typing import Optional

from . import events
from .engine.base import RuntimeState
from .runtime_manager import runtime_manager
from ..storage import db as storage_db
from ..storage.db import MetricsSnapshot, Runtime

DEFAULT_INTERVAL_SECONDS = 15.0
DEFAULT_RETENTION_PER_RUNTIME = 10_000  # ~41 hours of history at the default 15s interval


class MetricsRecorder:
    def __init__(
        self,
        *,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        retention_per_runtime: int = DEFAULT_RETENTION_PER_RUNTIME,
    ) -> None:
        self.interval = interval
        self.retention_per_runtime = retention_per_runtime
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Idempotent: calling start() while already running is a no-op,
        same as McpManager/PermissionEngine's lazy-thread pattern."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="metrics-recorder", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - one bad sample must never kill the background loop
                pass
            self._stop.wait(self.interval)

    def tick(self) -> int:
        """One sampling pass: writes a snapshot for every ONLINE runtime
        and prunes old rows. Public and synchronous (not just the
        private loop body) so a caller -- tests, or a manual "sample
        now" trigger -- can run exactly one pass without waiting on the
        background thread. Returns how many rows were written."""
        hw = runtime_manager.get_hardware_snapshot()
        cpu, mem, gpu = hw["cpu"], hw["memory"], hw["gpu"]
        written = 0
        with storage_db.SessionLocal() as db:
            runtime_ids: list[int] = []
            for rt in db.query(Runtime).all():
                status = runtime_manager.get_status(db, rt)
                if status.state != RuntimeState.ONLINE:
                    continue
                m = runtime_manager.get_metrics(rt)
                gpu_percent = gpu.utilization_percent if gpu.available else None
                vram_used_mb = gpu.vram_used_mb if gpu.available else None
                vram_total_mb = gpu.vram_total_mb if gpu.available else None
                snap = MetricsSnapshot(
                    runtime_id=rt.id,
                    cpu_percent=cpu.percent,
                    ram_used_mb=mem.used_mb,
                    ram_total_mb=mem.total_mb,
                    gpu_percent=gpu_percent,
                    vram_used_mb=vram_used_mb,
                    vram_total_mb=vram_total_mb,
                    tokens_per_sec=m.tokens_per_sec,
                    last_latency_ms=m.last_latency_ms,
                )
                db.add(snap)
                runtime_ids.append(rt.id)
                written += 1
                # One event per runtime (not one shared event) so a listener
                # can filter by runtime_id, same as every other per-entity
                # event in this app -- a future Notifications panel reading
                # "high GPU usage on runtime X" shouldn't have to unpack a
                # list to find out which runtime that was.
                events.emit(
                    events.EventType.RUNTIME_METRICS,
                    runtime_id=rt.id,
                    metadata={
                        "tokens_per_sec": m.tokens_per_sec,
                        "last_latency_ms": m.last_latency_ms,
                        "cpu_percent": cpu.percent,
                        "ram_used_mb": mem.used_mb,
                        "ram_total_mb": mem.total_mb,
                        "gpu_percent": gpu_percent,
                        "vram_used_mb": vram_used_mb,
                        "vram_total_mb": vram_total_mb,
                    },
                )
            db.commit()
            if written:
                self._prune(db, runtime_ids)
        return written

    def _prune(self, db, runtime_ids: list[int]) -> None:
        for runtime_id in runtime_ids:
            total = (
                db.query(MetricsSnapshot).filter(MetricsSnapshot.runtime_id == runtime_id).count()
            )
            overflow = total - self.retention_per_runtime
            if overflow <= 0:
                continue
            stale_ids = [
                row.id
                for row in db.query(MetricsSnapshot.id)
                .filter(MetricsSnapshot.runtime_id == runtime_id)
                .order_by(MetricsSnapshot.id)
                .limit(overflow)
                .all()
            ]
            db.query(MetricsSnapshot).filter(MetricsSnapshot.id.in_(stale_ids)).delete(synchronize_session=False)
        db.commit()


# One process, one recorder -- same reasoning as runtime_manager/mcp_manager.
metrics_recorder = MetricsRecorder()
