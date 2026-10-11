"""
backend/core/idle_policy.py

Give the memory back when the work is over.

llama-server keeps the model in RAM/VRAM for as long as it runs, even if
nothing has talked to it for hours. This module decides -- per runtime, from
a policy the user chooses -- when to stop it so the GPU is free again.

Policy (stored per runtime, see storage in api/http.py):
    {"mode": "off"}                              never stop automatically (default)
    {"mode": "after_agent", "grace_minutes": 2}  stop shortly after the LAST agent
                                                 terminal on this runtime closes
    {"mode": "idle", "idle_minutes": 10}         stop after N minutes with no activity

"Activity" = a request being processed (/slots busy) or an agent terminal open.
Nothing here guesses: if busy-ness can't be read (None) the runtime is treated
as active, so a runtime is never stopped on missing information.

`decide()` is a pure function (every threshold testable without a clock or a
process). `AgentSessionRegistry` counts open agent terminals. `IdleReaper` is
the background loop that applies the decision.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

MODES = ("off", "after_agent", "idle")
DEFAULT_POLICY = {"mode": "off", "grace_minutes": 2, "idle_minutes": 10}
MIN_MINUTES, MAX_MINUTES = 1, 24 * 60


class PolicyError(ValueError):
    pass


def normalize_policy(raw) -> dict:
    """Validated policy with defaults filled in; raises PolicyError."""
    if raw is None:
        return dict(DEFAULT_POLICY)
    if not isinstance(raw, dict):
        raise PolicyError("The policy must be an object.")
    mode = raw.get("mode", "off")
    if mode not in MODES:
        raise PolicyError(f"mode must be one of: {', '.join(MODES)}.")
    out = dict(DEFAULT_POLICY)
    out["mode"] = mode
    for key in ("grace_minutes", "idle_minutes"):
        if key in raw and raw[key] is not None:
            v = raw[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not (MIN_MINUTES <= v <= MAX_MINUTES):
                raise PolicyError(f"{key} must be a number between {MIN_MINUTES} and {MAX_MINUTES}.")
            out[key] = v
    return out


def parse_stored(text: Optional[str]) -> dict:
    """A stored value that no longer parses means 'off', never an error loop."""
    if not text:
        return dict(DEFAULT_POLICY)
    try:
        return normalize_policy(json.loads(text))
    except (ValueError, PolicyError):
        return dict(DEFAULT_POLICY)


@dataclass(frozen=True)
class Decision:
    stop: bool
    reason: str


def decide(
    policy: dict,
    *,
    now: float,
    last_active: float,
    terminals_open: int,
    busy: Optional[bool],
    agent_seen: bool,
    last_agent_close: Optional[float],
) -> Decision:
    mode = policy.get("mode", "off")
    if mode == "off":
        return Decision(False, "policy is off")
    if terminals_open > 0:
        return Decision(False, "an agent terminal is open")
    if busy is None or busy:
        return Decision(False, "the server is busy or its state is unknown")

    if mode == "after_agent":
        if not agent_seen or last_agent_close is None:
            return Decision(False, "no agent has used this runtime since it started")
        waited = now - last_agent_close
        grace = float(policy.get("grace_minutes", 2)) * 60
        if waited >= grace:
            return Decision(True, f"the last agent closed {int(waited // 60)} min ago")
        return Decision(False, "waiting out the grace period")

    idle_for = now - last_active
    limit = float(policy.get("idle_minutes", 10)) * 60
    if idle_for >= limit:
        return Decision(True, f"idle for {int(idle_for // 60)} min")
    return Decision(False, "still within the idle limit")


class AgentSessionRegistry:
    """Open agent terminals per runtime. Thread-safe (the websocket handler
    writes, the reaper thread reads)."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._open: dict[int, int] = {}
        self._last_close: dict[int, float] = {}
        self._seen: set[int] = set()

    def opened(self, runtime_id: int) -> None:
        with self._lock:
            self._open[runtime_id] = self._open.get(runtime_id, 0) + 1
            self._seen.add(runtime_id)

    def closed(self, runtime_id: int) -> None:
        with self._lock:
            n = self._open.get(runtime_id, 0)
            if n <= 0:
                return  # a double close must not push the count negative
            self._open[runtime_id] = n - 1
            self._last_close[runtime_id] = self._clock()

    def open_count(self, runtime_id: int) -> int:
        with self._lock:
            return self._open.get(runtime_id, 0)

    def last_close(self, runtime_id: int) -> Optional[float]:
        with self._lock:
            return self._last_close.get(runtime_id)

    def seen(self, runtime_id: int) -> bool:
        with self._lock:
            return runtime_id in self._seen

    def forget(self, runtime_id: int) -> None:
        """Called when the runtime stops: the next start begins with a clean slate."""
        with self._lock:
            self._seen.discard(runtime_id)
            self._last_close.pop(runtime_id, None)


agent_sessions = AgentSessionRegistry()


class IdleReaper:
    """Background loop. Dependencies are injected so the logic is testable
    without a database, a thread or a real llama-server."""

    def __init__(
        self,
        *,
        list_online: Callable[[], list[int]],
        get_policy: Callable[[int], dict],
        is_busy: Callable[[int], Optional[bool]],
        stop_runtime: Callable[[int, str], None],
        registry: AgentSessionRegistry = agent_sessions,
        clock: Callable[[], float] = time.time,
        interval: float = 15.0,
    ) -> None:
        self._list_online, self._get_policy, self._is_busy, self._stop_runtime = list_online, get_policy, is_busy, stop_runtime
        self._registry, self._clock, self.interval = registry, clock, interval
        self._last_active: dict[int, float] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def tick(self) -> list[int]:
        """One pass; returns the runtime ids it stopped."""
        now = self._clock()
        online = self._list_online()
        for gone in set(self._last_active) - set(online):
            self._last_active.pop(gone, None)  # stopped some other way: forget it
            self._registry.forget(gone)
        stopped = []
        for rid in online:
            self._last_active.setdefault(rid, now)  # first sighting counts as activity
            policy = self._get_policy(rid)
            if policy.get("mode") == "off":
                continue
            busy = self._is_busy(rid)
            if busy is None or busy or self._registry.open_count(rid) > 0:
                self._last_active[rid] = now
            d = decide(
                policy, now=now, last_active=self._last_active[rid],
                terminals_open=self._registry.open_count(rid), busy=busy,
                agent_seen=self._registry.seen(rid), last_agent_close=self._registry.last_close(rid),
            )
            if d.stop:
                try:
                    self._stop_runtime(rid, d.reason)
                except Exception:  # noqa: BLE001 - one failed stop must not block the others
                    continue
                self._last_active.pop(rid, None)
                self._registry.forget(rid)
                stopped.append(rid)
        return stopped

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="idle-reaper", daemon=True)
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
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(self.interval)
