"""
tests/backend/test_pty_session_windows.py

Regression for "terminal connected but nothing shows / typing does nothing"
on Windows. The old PtySession called pywinpty's read(max_bytes,
blocking=False); the real signature is read(size) and BLOCKS, so every call
raised TypeError, which was swallowed -> zero output, ever.

We can't import real pywinpty on Linux, so a fake `winpty` module mimics its
documented contract exactly: read(size=1024) takes no other argument, blocks
until data, raises EOFError when closed; write(str); isalive(); setwinsize().
Passing `blocking=` to the fake raises TypeError just like the real one.
"""

import sys
import threading
import time
import types

import pytest

from backend.core.agents import pty_session


class _FakePtyProcess:
    def __init__(self):
        self._lock = threading.Condition()
        self._chunks: list[str] = ["banner\r\n"]
        self._closed = False
        self.written: list[str] = []
        self.size = None
        self.fail_with: Exception | None = None

    @classmethod
    def spawn(cls, argv, cwd=None, env=None, dimensions=(24, 80)):
        p = cls()
        p.argv = argv
        p.size = dimensions
        return p

    def read(self, size=1024):  # no blocking= kwarg, like the real one
        with self._lock:
            while not self._chunks and not self._closed and self.fail_with is None:
                self._lock.wait(0.05)
            if self.fail_with is not None:
                raise self.fail_with
            if self._chunks:
                return self._chunks.pop(0)
            raise EOFError()

    def write(self, s):
        self.written.append(s)
        with self._lock:
            self._chunks.append("echo:" + s)
            self._lock.notify_all()
        return len(s)

    def isalive(self):
        return not self._closed

    def setwinsize(self, rows, cols):
        self.size = (rows, cols)

    def close(self):
        with self._lock:
            self._closed = True
            self._lock.notify_all()


@pytest.fixture
def win_session(monkeypatch):
    fake = types.ModuleType("winpty")
    fake.PtyProcess = _FakePtyProcess
    monkeypatch.setitem(sys.modules, "winpty", fake)
    monkeypatch.setattr(pty_session, "IS_WINDOWS", True)
    s = pty_session.PtySession(["hermes"], cols=100, rows=30)
    yield s
    s.close()


def _read_until(session, needle: bytes, seconds: float = 3.0) -> bytes:
    buf = b""
    end = time.time() + seconds
    while time.time() < end and needle not in buf:
        buf += session.read(65536, 0.1)
    return buf


def test_banner_arrives_through_reader_thread(win_session):
    assert b"banner" in _read_until(win_session, b"banner")
    assert win_session.read_error is None


def test_typed_input_round_trips(win_session):
    _read_until(win_session, b"banner")
    win_session.write("hello\r".encode())
    assert b"echo:hello" in _read_until(win_session, b"echo:hello")
    assert win_session._proc.written == ["hello\r"]


def test_spawn_dimensions_and_resize(win_session):
    assert win_session._proc.size == (30, 100)  # (rows, cols)
    win_session.resize(120, 40)
    assert win_session._proc.size == (40, 120)


def test_read_never_blocks_long_when_idle(win_session):
    _read_until(win_session, b"banner")
    t0 = time.time()
    assert win_session.read(65536, 0.1) == b""
    assert time.time() - t0 < 1.0


def test_unexpected_reader_error_is_surfaced_not_swallowed(monkeypatch):
    fake = types.ModuleType("winpty")
    fake.PtyProcess = _FakePtyProcess
    monkeypatch.setitem(sys.modules, "winpty", fake)
    monkeypatch.setattr(pty_session, "IS_WINDOWS", True)
    s = pty_session.PtySession(["hermes"])
    try:
        _read_until(s, b"banner")
        s._proc.fail_with = OSError("conpty broke")
        end = time.time() + 3
        while s.read_error is None and time.time() < end:
            time.sleep(0.05)
        assert s.read_error and "conpty broke" in s.read_error
    finally:
        s.close()


def test_eof_is_a_normal_end_not_an_error(win_session):
    _read_until(win_session, b"banner")
    win_session._proc.close()
    time.sleep(0.3)
    assert win_session.read_error is None
    assert win_session.is_alive() is False


def test_write_to_dead_process_does_not_raise(win_session):
    def boom(_s):
        raise OSError("gone")

    win_session._proc.write = boom
    win_session.write(b"x")  # must not propagate into the websocket loop
