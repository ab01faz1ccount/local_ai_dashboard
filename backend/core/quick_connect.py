"""
backend/core/quick_connect.py

"Use this model" in one step: turn a model + this machine's hardware into a
ready-to-start runtime definition. Pure functions (the API layer does the
database work), so every rule is testable.

Nothing here is specific to a model family or an agent: it reads the file size
and the hardware snapshot, and defers to core/model_recommend.py for the
settings. The user can still change every value afterwards in Settings.
"""

from __future__ import annotations

from typing import Optional

from . import model_recommend

DEFAULT_START_PORT = 8080
MAX_PORT = 65535


class QuickConnectError(ValueError):
    pass


def next_free_port(used: set[int], start: int = DEFAULT_START_PORT) -> int:
    port = start
    while port in used:
        port += 1
    if port > MAX_PORT:
        raise QuickConnectError("No free port left to give this runtime.")
    return port


def pick_executable(existing_paths: list[str], detected: Optional[str]) -> str:
    """Reuse the llama-server the user already uses; else the auto-detected one."""
    for p in existing_paths:
        if p:
            return p
    if detected:
        return detected
    raise QuickConnectError(
        "llama-server was not found. Install llama.cpp or choose its path in Setup, then try again."
    )


def runtime_for_model(
    *,
    model_name: str,
    file_size_bytes: float,
    trained_context: Optional[int],
    hardware: Optional[dict],
    executable_path: str,
    used_ports: set[int],
    for_agent: bool = True,
) -> dict:
    """Keyword arguments for creating a Runtime row, plus the reasons."""
    suggestion = model_recommend.suggest_launch_config(
        file_size_bytes, hardware, trained_context=trained_context, for_agent=for_agent
    )
    return {
        "runtime": {
            "name": f"{model_name}",
            "executable_path": executable_path,
            "host": "127.0.0.1",
            "port": next_free_port(used_ports),
            "config_json": dict(suggestion["config"]),
        },
        "fit": suggestion["fit"],
        "notes": suggestion["notes"],
    }
