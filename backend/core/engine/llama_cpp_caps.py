"""
backend/core/engine/llama_cpp_caps.py

What THIS llama-server build understands, learned from the binary itself
(`llama-server --help`) instead of assumed.

First use: `-fa/--flash-attn`. Older builds take it as a bare switch; newer
builds take a value (`--flash-attn [on|off|auto]`, default auto) and a bare
`--flash-attn` there swallows the next argument or errors. The right syntax
depends on the binary the user actually installed, so we ask it.
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional

HELP_TIMEOUT_SECONDS = 15

# executable path -> "takes a value" (only successful probes are cached)
_FLASH_ATTN_CACHE: dict[str, bool] = {}

_VALUE_STYLE = re.compile(r"on\|off\|auto|'on',\s*'off',\s*or\s*'auto'", re.IGNORECASE)


def parse_flash_attn_takes_value(help_text: str) -> Optional[bool]:
    """True: the new `--flash-attn [on|off|auto]` form. False: the old bare
    switch. None: the help text doesn't mention flash attention at all."""
    lines = [ln for ln in help_text.splitlines() if "flash-attn" in ln.lower() or "flash attention" in ln.lower()]
    if not lines:
        return None
    return bool(_VALUE_STYLE.search(" ".join(lines)))


def flash_attn_takes_value(executable_path: str) -> bool:
    """Whether `--flash-attn` must be given a value for this binary. If the
    binary can't be probed we assume the NEW style: the models people run
    through this app today (e.g. Qwen3.5) need a recent llama.cpp anyway, and
    a failed probe means the launch itself is about to fail with a clearer
    error than a flag-syntax one."""
    cached = _FLASH_ATTN_CACHE.get(executable_path)
    if cached is not None:
        return cached
    try:
        result = subprocess.run(
            [executable_path, "--help"], capture_output=True, text=True, timeout=HELP_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    text = f"{result.stdout or ''}\n{result.stderr or ''}"
    if not text.strip():
        return True
    parsed = parse_flash_attn_takes_value(text)
    value = True if parsed is None else parsed
    _FLASH_ATTN_CACHE[executable_path] = value
    return value


# ---- which compute devices this build can actually use --------------------

_DEVICE_CACHE: dict[tuple[str, float], dict] = {}
_DEVICE_LINE = re.compile(r"^\s+([A-Za-z][A-Za-z0-9_]*?\d*):\s+(.+?)\s*$")


def parse_list_devices(text: str) -> list[dict]:
    """Devices from `llama-server --list-devices`. Lines look like
    `  CUDA0: NVIDIA GeForce RTX 5060 Ti (16310 MiB, 15000 MiB free)`."""
    devices = []
    for line in text.splitlines():
        m = _DEVICE_LINE.match(line)
        if not m:
            continue
        dev_id, rest = m.group(1), m.group(2)
        if dev_id.lower() in {"error", "warning", "note", "info", "usage"}:
            continue
        devices.append({"id": dev_id, "description": rest, "is_gpu": not dev_id.upper().startswith("CPU")})
    return devices


def probe_devices(executable_path: str) -> dict:
    """{"probed": bool, "devices": [...], "gpus": [...]}. `probed` is False when
    the binary could not be run at all -- "unknown", never "no GPU"."""
    import os

    try:
        key = (executable_path, os.path.getmtime(executable_path))
    except OSError:
        key = None
    if key and key in _DEVICE_CACHE:
        return _DEVICE_CACHE[key]
    try:
        result = subprocess.run(
            [executable_path, "--list-devices"], capture_output=True, text=True, timeout=HELP_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"probed": False, "devices": [], "gpus": []}
    text = f"{result.stdout or ''}\n{result.stderr or ''}"
    devices = parse_list_devices(text)
    report = {"probed": True, "devices": devices, "gpus": [d for d in devices if d["is_gpu"]]}
    if key:
        _DEVICE_CACHE[key] = report
    return report
