"""
backend/core/platform/linux/provider.py

LinuxPlatformProvider: the Linux half of the PlatformProvider contract.
Metrics are delegated to `_common` (psutil/pynvml are already
cross-platform); what's genuinely Linux-specific here is process
management -- launching in its own process group via `os.setsid` so a
SIGTERM/SIGKILL to the group also reaps any children llama-server spawns,
and a graceful-then-forceful shutdown sequence.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections import deque
from typing import Optional

from .. import _common
from ..base import CpuMetrics, GpuMetrics, MemoryMetrics, PlatformProvider


class LinuxPlatformProvider(PlatformProvider):
    def __init__(self) -> None:
        self._processes: dict[int, subprocess.Popen] = {}
        self._logs: dict[int, deque] = {}

    # -- metrics (delegated) --------------------------------------------

    def get_cpu_metrics(self) -> CpuMetrics:
        return _common.get_cpu_metrics()

    def get_memory_metrics(self) -> MemoryMetrics:
        return _common.get_memory_metrics()

    def get_gpu_metrics(self) -> GpuMetrics:
        return _common.get_gpu_metrics()

    # -- process management ----------------------------------------------

    def start_process(self, executable_path: str, args: list[str], cwd: Optional[str] = None) -> int:
        proc = subprocess.Popen(
            [executable_path, *args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            preexec_fn=os.setsid,  # own process group -> can signal the whole tree
        )
        self._processes[proc.pid] = proc
        self._logs[proc.pid] = deque(maxlen=2000)
        threading.Thread(target=self._pump_logs, args=(proc,), daemon=True).start()
        return proc.pid

    def _pump_logs(self, proc: subprocess.Popen) -> None:
        buf = self._logs.get(proc.pid)
        if proc.stdout is None or buf is None:
            return
        # Reading line-by-line off a crashed/closed pipe just ends the
        # iterator -- this thread exits quietly, it never raises into the
        # engine layer (isolate-crashes requirement).
        for line in proc.stdout:
            buf.append(line.rstrip("\n"))

    def stop_process(self, pid: int, timeout: float = 10.0) -> bool:
        proc = self._processes.get(pid)
        if proc is None or proc.poll() is not None:
            return True  # already stopped / never tracked -> nothing to do

        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            return True

        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                return True
            time.sleep(0.2)

        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        return proc.poll() is not None

    def is_process_running(self, pid: int) -> bool:
        proc = self._processes.get(pid)
        return proc is not None and proc.poll() is None

    def get_process_logs(self, pid: int, n: int = 200) -> list[str]:
        buf = self._logs.get(pid)
        if not buf:
            return []
        return list(buf)[-n:]
