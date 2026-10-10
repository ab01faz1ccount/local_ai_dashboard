"""
backend/core/agents/pairing.py

The end-to-end proof that an agent is really talking to a runtime: not what
its config *says*, but what the llama-server actually *received*.

How: llama-server logs every HTTP request it serves. While an agent's
terminal is open we watch that runtime's log tail for inference requests
(`POST /v1/chat/completions` and friends). The first one that shows up
after we started watching = the pairing is real. This works for any agent,
because it only looks at the server side.

Honest limits (surfaced to the UI, not hidden):
  * It can't tell WHICH client sent the request. The dashboard's own Chat
    tab on the same runtime would also count -- so use the agent terminal.
  * "waiting" means "no request seen yet", not "failed": a llama-server build
    that doesn't log requests at its current verbosity never produces one.
  * The log buffer is a bounded window (2000 lines). We therefore read it
    incrementally (overlap of the previous window), not by counting.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Optional

# Inference endpoints only: a GET /slots poll, /health, /props or the
# dashboard's own /tokenize must never look like "the agent talked".
_INFERENCE_LINE = re.compile(
    r"POST\s+(?:https?://\S+?)?/(?:v1/)?(?:chat/)?completions?\b|POST\s+/v1/responses\b"
    # Newer llama-server builds don't log the HTTP line at all; a request shows
    # up as a slot picking up a task -- and does so when it STARTS, not ends.
    r"|\bslot\s+launch_slot_\S*\s+id\s+\d+\s*\|\s*task\s+\d+\s*\|\s*processing task",
    re.IGNORECASE,
)

# How much of the previous window must reappear in the new one for us to be
# sure the two windows line up (so only genuinely new lines are inspected).
_OVERLAP_LINES = 20


def is_inference_request_line(line: str) -> bool:
    return bool(_INFERENCE_LINE.search(line))


def new_lines_since(previous: list[str], current: list[str]) -> Optional[list[str]]:
    """Lines in `current` that weren't in `previous`, or None if the windows
    can't be aligned (log buffer rotated past us, or the runtime restarted)
    -- in which case the caller must re-baseline rather than guess."""
    if not previous:
        return list(current)
    tail = previous[-_OVERLAP_LINES:]
    # Longest suffix of `tail` that occurs in `current`, searched from the end
    # so the most recent alignment wins. Require >= 3 lines of agreement (or
    # the whole tail if it is shorter) so one common repeated line can't
    # fake an alignment and make us skip genuinely new lines.
    min_size = min(len(tail), 3)
    for size in range(len(tail), min_size - 1, -1):
        probe = tail[-size:]
        for start in range(len(current) - size, -1, -1):
            if current[start : start + size] == probe:
                return current[start + size :]
    return None


class PairingMonitor:
    """Per (agent, runtime) watch. Thread-safe: the websocket handler starts
    it, the REST poll endpoint reads it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._watches: dict[tuple[int, int], dict] = {}

    def start(self, agent_id: int, runtime_id: int, log_lines: list[str], verification: Optional[dict] = None) -> None:
        with self._lock:
            self._watches[(agent_id, runtime_id)] = {
                "started_at": time.time(),
                "last_lines": list(log_lines),
                "requests": 0,
                "first_request_at": None,
                "last_request_at": None,
                "rebaselined": False,
                "verification": verification,
            }

    def poll(self, agent_id: int, runtime_id: int, log_lines: list[str], busy: Optional[bool] = None) -> dict:
        with self._lock:
            w = self._watches.get((agent_id, runtime_id))
            if w is None:
                return {"watching": False, "status": "not_watching", "requests": 0, "verification": None}

            fresh = new_lines_since(w["last_lines"], log_lines)
            if fresh is None:
                w["rebaselined"] = True  # can't align: don't guess, just resume from here
            else:
                hits = sum(1 for line in fresh if is_inference_request_line(line))
                if hits:
                    now = time.time()
                    w["requests"] += hits
                    w["first_request_at"] = w["first_request_at"] or now
                    w["last_request_at"] = now
            w["last_lines"] = list(log_lines)

            return {
                "watching": True,
                # latched: once seen, stays "paired" even if the log rotates
                # llama-server logs a request only when it FINISHES, so a long
                # prompt (minutes on a CPU) shows nothing for a long time: if
                # /slots says the server is mid-request, say so instead of "waiting".
                "status": "paired" if w["requests"] > 0 else ("in_flight" if busy else "waiting"),
                "requests": w["requests"],
                "started_at": w["started_at"],
                "first_request_at": w["first_request_at"],
                "last_request_at": w["last_request_at"],
                "rebaselined": w["rebaselined"],
                "verification": w["verification"],
            }

    def stop(self, agent_id: int, runtime_id: int) -> None:
        with self._lock:
            self._watches.pop((agent_id, runtime_id), None)


pairing_monitor = PairingMonitor()
