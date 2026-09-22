"""
backend/core/platform/windows/provider.py

WindowsPlatformProvider: the Windows half of the PlatformProvider contract.
Metrics are delegated to `_common`. Process management uses
CREATE_NEW_PROCESS_GROUP + CTRL_BREAK_EVENT for a genuine graceful-shutdown
attempt (plain TerminateProcess is a hard kill on Windows, not a request),
falling back to psutil's hard kill if the process ignores it.
"""

from __future__ import annotations

import signal
import subprocess
import threading
import time
from collections import deque
from typing import Optional

import psutil

from .. import _common
from ..base import CpuMetrics, GpuMetrics, MemoryMetrics, PlatformProvider

CREATE_NEW_PROCESS_GROUP = 0x00000200


class WindowsPlatformProvider(PlatformProvider):
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
            creationflags=CREATE_NEW_PROCESS_GROUP,
        )
        self._processes[proc.pid] = proc
        self._logs[proc.pid] = deque(maxlen=2000)
        threading.Thread(target=self._pump_logs, args=(proc,), daemon=True).start()
        return proc.pid

    def _pump_logs(self, proc: subprocess.Popen) -> None:
        buf = self._logs.get(proc.pid)
        if proc.stdout is None or buf is None:
            return
        for line in proc.stdout:
            buf.append(line.rstrip("\n"))

    def stop_process(self, pid: int, timeout: float = 10.0) -> bool:
        proc = self._processes.get(pid)
        if proc is None or proc.poll() is not None:
            return True

        try:
            # Requires CREATE_NEW_PROCESS_GROUP at launch; lets llama-server
            # shut down cleanly (flush logs, close the socket) instead of
            # being killed mid-request.
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        except Exception:
            pass

        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                return True
            time.sleep(0.2)

        try:
            psutil.Process(pid).kill()
        except psutil.NoSuchProcess:
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
