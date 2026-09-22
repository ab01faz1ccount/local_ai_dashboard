"""
backend/core/agents/pty_session.py

Cross-platform pseudo-terminal wrapper. This is what lets an agent CLI's
*actual* interactive behavior -- its real banner, colors, cursor
movement, interactive prompts -- come through exactly as it does in a
real terminal, rather than a flattened "capture stdout line by line"
approximation that would break on anything TUI-ish (progress bars,
readline-style input, ANSI color).

POSIX (Linux/Mac): stdlib `pty`, no extra dependency.
Windows: `pywinpty` (`pip install pywinpty`) -- Windows has no native
pty concept; pywinpty wraps the Windows Pseudo Console API (ConPTY),
which is what actually makes an interactive Windows CLI behave
correctly when driven from something that isn't a real console window.
"""

from __future__ import annotations

import os
import platform
import subprocess
from typing import Optional

IS_WINDOWS = platform.system() == "Windows"


class PtySession:
    """One running process attached to a pseudo-terminal. `read()` is a
    short-timeout poll meant to be called in a loop from a background
    thread (see api/ws.py's agent-terminal route) -- it never blocks
    for long, so that loop stays responsive to the websocket closing."""

    def __init__(
        self,
        command: list[str],
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        cols: int = 80,
        rows: int = 24,
    ):
        self.command = command
        self._closed = False
        self._backend = "winpty" if IS_WINDOWS else "posix"
        if IS_WINDOWS:
            self._init_windows(command, cwd, env, cols, rows)
        else:
            self._init_posix(command, cwd, env, cols, rows)

    # ---- Windows (pywinpty / ConPTY) ----
    def _init_windows(self, command, cwd, env, cols, rows) -> None:
        try:
            import winpty
        except ImportError as exc:
            raise RuntimeError(
                "pywinpty لازم است برای اجرای ترمینال agent روی ویندوز. نصب کن: pip install pywinpty"
            ) from exc
        self._proc = winpty.PtyProcess.spawn(command, cwd=cwd, env=env, dimensions=(rows, cols))

    # ---- POSIX (stdlib pty) ----
    def _init_posix(self, command, cwd, env, cols, rows) -> None:
        import pty

        pid, fd = pty.fork()
        if pid == 0:
            # Child process: replace ourselves with the target command.
            # Any exception here must exit immediately -- this is a
            # forked child, so a normal Python exception propagating up
            # would re-enter the parent's code paths, which is not safe.
            try:
                if cwd:
                    os.chdir(cwd)
                os.execvpe(command[0], command, env or dict(os.environ))
            except Exception:
                os._exit(1)
        self._pid = pid
        self._fd = fd
        self._set_size_posix(cols, rows)

    def _set_size_posix(self, cols: int, rows: int) -> None:
        import fcntl
        import struct
        import termios

        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)

    def read(self, max_bytes: int = 65536, timeout: float = 0.2) -> bytes:
        if self._closed:
            return b""
        if self._backend == "winpty":
            try:
                data = self._proc.read(max_bytes, blocking=False)
            except Exception:
                return b""
            if not data:
                return b""
            return data.encode("utf-8", errors="replace") if isinstance(data, str) else data

        import select

        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return b""
        try:
            return os.read(self._fd, max_bytes)
        except OSError:
            return b""

    def write(self, data: bytes) -> None:
        if self._closed:
            return
        if self._backend == "winpty":
            self._proc.write(data.decode("utf-8", errors="replace"))
        else:
            try:
                os.write(self._fd, data)
            except OSError:
                pass

    def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        if self._backend == "winpty":
            self._proc.setwinsize(rows, cols)
        else:
            self._set_size_posix(cols, rows)

    def is_alive(self) -> bool:
        if self._closed:
            return False
        if self._backend == "winpty":
            try:
                return self._proc.isalive()
            except Exception:
                return False
        try:
            pid, _status = os.waitpid(self._pid, os.WNOHANG)
            return pid == 0
        except ChildProcessError:
            return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._backend == "winpty":
            try:
                self._proc.close()
            except Exception:
                pass
        else:
            try:
                os.close(self._fd)
            except OSError:
                pass
            try:
                os.kill(self._pid, 9)
            except ProcessLookupError:
                pass
