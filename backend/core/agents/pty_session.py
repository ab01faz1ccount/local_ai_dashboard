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

Windows read model: pywinpty's `PtyProcess.read(size=1024)` is a
*blocking* call (it raises EOFError once the terminal closes) and has no
`blocking=` / timeout parameter. Calling it with extra arguments raises
TypeError -- which an earlier version of this file swallowed, so the
terminal looked connected but never showed a single byte (not even the
agent's banner). So on Windows we run the blocking read in one daemon
thread per session and hand the data to `read()` through a queue; that
gives `read()` the same "short poll, never blocks long" contract as the
POSIX path. Any unexpected reader failure is logged and exposed through
`read_error` instead of being silently dropped.
"""

from __future__ import annotations

import logging
import os
import platform
import queue
import threading
from typing import Optional

logger = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"

# Chunk size for the blocking pywinpty read. Deliberately modest: the
# library's own default is 1024, and a smaller chunk keeps latency low.
_WINPTY_READ_CHUNK = 4096


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
        # Set (once) when the output reader fails for a reason other than
        # the normal end-of-terminal; api/ws.py surfaces it to the UI.
        self.read_error: Optional[str] = None
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
        self._out_queue: "queue.Queue[bytes]" = queue.Queue()
        self._reader = threading.Thread(target=self._winpty_reader_loop, name="winpty-reader", daemon=True)
        self._reader.start()

    def _winpty_reader_loop(self) -> None:
        """Blocking read loop (own thread). Ends on EOF, on close(), or on
        an unexpected error -- the last one is recorded, never swallowed."""
        while not self._closed:
            try:
                data = self._proc.read(_WINPTY_READ_CHUNK)
            except EOFError:
                return  # the process's terminal closed: normal end of output
            except Exception as exc:  # noqa: BLE001 - recorded, see read_error
                if not self._closed:
                    self.read_error = f"خواندن خروجی ترمینال شکست خورد: {exc!r}"
                    logger.exception("pywinpty read failed")
                return
            if not data:
                continue  # pywinpty can return '' for internal frames
            self._out_queue.put(data.encode("utf-8", errors="replace") if isinstance(data, str) else data)

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
                chunk = self._out_queue.get(timeout=timeout)
            except queue.Empty:
                return b""
            # Drain whatever else is already waiting, up to max_bytes, so a
            # burst (e.g. the banner) goes out as one frame instead of many.
            while len(chunk) < max_bytes:
                try:
                    chunk += self._out_queue.get_nowait()
                except queue.Empty:
                    break
            return chunk

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
            try:
                self._proc.write(data.decode("utf-8", errors="replace"))
            except Exception:  # noqa: BLE001 - a dead process must not kill the websocket loop
                logger.exception("pywinpty write failed")
        else:
            try:
                os.write(self._fd, data)
            except OSError:
                pass

    def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        if self._backend == "winpty":
            try:
                self._proc.setwinsize(rows, cols)
            except Exception:  # noqa: BLE001
                logger.exception("pywinpty resize failed")
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

    def _root_pid(self) -> Optional[int]:
        try:
            return int(self._proc.pid) if self._backend == "winpty" else int(self._pid)
        except Exception:  # noqa: BLE001
            return None

    def _kill_process_tree(self) -> None:
        """Kill the agent AND everything it started (tool servers, shells, its
        own workers). Closing only the pty / the top pid left those running
        after the user deleted the chat or left the terminal -- an agent kept
        working (and kept hitting llama-server) behind the UI's back."""
        pid = self._root_pid()
        if pid is None:
            return
        try:
            import psutil
        except ImportError:
            return
        try:
            root = psutil.Process(pid)
            victims = root.children(recursive=True) + [root]
        except psutil.Error:
            return
        for proc in victims:
            try:
                proc.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(victims, timeout=3)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._kill_process_tree()
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
