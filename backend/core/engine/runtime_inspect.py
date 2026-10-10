"""
backend/core/engine/runtime_inspect.py

PROOF of what a llama-server process really did -- not what the Settings page
says it was told. Three independent sources, compared:

  1. the command line the app actually launched (every flag),
  2. what the server itself reported at startup (its log),
  3. what the live server answers on `/props`.

Pure functions only (text in, dict out) so every case is unit-testable; the
I/O (reading the log, calling /props, probing devices) lives with the caller.

Why it exists: a runtime was configured with `-ngl 99` and every request still
ran on the CPU (~50 prompt tok/s). The build had no GPU backend at all and
nothing in the app said so.
"""

from __future__ import annotations

import re
from typing import Optional

OK = "ok"
MISMATCH = "mismatch"
UNKNOWN = "unknown"

GPU = "gpu"
PARTIAL = "partial"
CPU = "cpu"
PLACEMENT_UNKNOWN = "unknown"

# ---- log parsing ---------------------------------------------------------

_RE_THREADS = re.compile(r"n_threads\s*=\s*(\d+)")
_RE_SLOTS = re.compile(r"n_slots\s*=\s*(\d+)")
_RE_CTX_SLOT = re.compile(r"n_ctx_slot\s*=\s*(\d+)")
_RE_CTX = re.compile(r"\bn_ctx\s*=\s*(\d+)")
_RE_OFFLOADED = re.compile(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers\s+to\s+GPU", re.IGNORECASE)
_RE_BACKEND = re.compile(r"load_backend:\s*loaded\s+(\S+)\s+backend", re.IGNORECASE)
_RE_CUDA_FOUND = re.compile(r"ggml_cuda_init:\s*found\s+(\d+)\s+CUDA\s+devices?", re.IGNORECASE)
_RE_USING_DEVICE = re.compile(r"using device\s+(\S+)\s*\(([^)]*)\)", re.IGNORECASE)
_RE_BUFFER = re.compile(r"(\S+)\s+(model|KV)\s+buffer size\s*=\s*([\d.]+)\s*MiB", re.IGNORECASE)


def _is_cpu_buffer(name: str) -> bool:
    return name.upper().startswith("CPU")


def parse_startup_log(lines: list[str]) -> dict:
    """What llama-server said while loading. The log buffer belongs to ONE
    process (a restart starts a fresh one), so the whole buffer is one start --
    and the backend lines come BEFORE the `loading model` line, so the buffer
    must not be cut at it."""
    out: dict = {
        "threads": None,
        "slots": None,
        "ctx_per_slot": None,
        "ctx_total": None,
        "layers_offloaded": None,
        "layers_total": None,
        "backends_loaded": [],
        "cuda_devices_found": None,
        "devices_used": [],
        "model_mib": {"gpu": 0.0, "cpu": 0.0},
        "kv_mib": {"gpu": 0.0, "cpu": 0.0},
        "saw_model_load": any("load_model" in ln for ln in lines),
    }
    for line in lines:
        if (m := _RE_THREADS.search(line)):
            out["threads"] = int(m.group(1))
        if (m := _RE_SLOTS.search(line)):
            out["slots"] = int(m.group(1))
        if (m := _RE_CTX_SLOT.search(line)):
            out["ctx_per_slot"] = int(m.group(1))
        elif (m := _RE_CTX.search(line)):
            out["ctx_total"] = int(m.group(1))
        if (m := _RE_OFFLOADED.search(line)):
            out["layers_offloaded"], out["layers_total"] = int(m.group(1)), int(m.group(2))
        if (m := _RE_BACKEND.search(line)):
            name = m.group(1).upper()
            if name not in out["backends_loaded"]:
                out["backends_loaded"].append(name)
        if (m := _RE_CUDA_FOUND.search(line)):
            out["cuda_devices_found"] = int(m.group(1))
        if (m := _RE_USING_DEVICE.search(line)):
            dev = f"{m.group(1)} ({m.group(2).split(' - ')[0].strip()})"
            if dev not in out["devices_used"]:
                out["devices_used"].append(dev)
        if (m := _RE_BUFFER.search(line)):
            kind = "model_mib" if m.group(2).lower() == "model" else "kv_mib"
            side = "cpu" if _is_cpu_buffer(m.group(1)) else "gpu"
            out[kind][side] += float(m.group(3))
    return out


def placement(observed: dict) -> str:
    """Where the model's weights really are: gpu / partial / cpu / unknown."""
    off, total = observed.get("layers_offloaded"), observed.get("layers_total")
    if off is not None and total:
        if off == 0:
            return CPU
        return GPU if off >= total else PARTIAL
    gpu_mib = observed["model_mib"]["gpu"]
    cpu_mib = observed["model_mib"]["cpu"]
    if gpu_mib or cpu_mib:
        if gpu_mib == 0:
            return CPU
        return GPU if cpu_mib < 0.05 * (gpu_mib + cpu_mib) else PARTIAL
    backends = observed.get("backends_loaded") or []
    if backends and all(b == "CPU" for b in backends):
        return CPU  # the build only has a CPU backend: nothing can go to a GPU
    return PLACEMENT_UNKNOWN


# ---- launch command ------------------------------------------------------

def flag_value(command: list[str], *flags: str) -> Optional[str]:
    """The value following any of `flags` in an argv list (None if absent)."""
    for i, arg in enumerate(command):
        if arg in flags and i + 1 < len(command):
            return command[i + 1]
    return None


def _check(key, status, expected, actual, detail=""):
    return {"key": key, "status": status, "expected": expected, "actual": actual, "detail": detail}


def _compare_number(key: str, expected, actual, source: str) -> dict:
    if actual is None:
        return _check(key, UNKNOWN, expected, None, f"{source}: not reported")
    try:
        same = int(float(expected)) == int(actual)
    except (TypeError, ValueError):
        same = str(expected) == str(actual)
    return _check(key, OK if same else MISMATCH, expected, actual, source)


def build_report(
    *,
    config: dict,
    command: Optional[list[str]],
    log_lines: list[str],
    props: Optional[dict] = None,
    devices: Optional[dict] = None,
) -> dict:
    """The full comparison: stored config vs launched command vs what the
    server reports. `devices` is the result of probing `llama-server
    --list-devices` (see llama_cpp_caps.probe_devices) or None."""
    observed = parse_startup_log(log_lines)
    place = placement(observed)
    checks: list[dict] = []

    # 1) was the setting even put on the command line?
    flag_for = {
        "n_gpu_layers": ("--n-gpu-layers", "-ngl"),
        "ctx_size": ("--ctx-size", "-c"),
        "threads": ("--threads", "-t"),
        "parallel_slots": ("--parallel", "-np"),
        "batch_size": ("--batch-size", "-b"),
        "ubatch_size": ("--ubatch-size", "-ub"),
    }
    if command is not None:
        for key, flags in flag_for.items():
            if config.get(key) in (None, ""):
                continue
            sent = flag_value(command, *flags)
            checks.append(
                _check(
                    f"{key} -> command line",
                    OK if sent is not None and str(sent) == str(config[key]) else MISMATCH,
                    str(config[key]),
                    sent,
                    "flag passed to llama-server" if sent is not None else "flag is NOT on the command line",
                )
            )

    # 2) did the server apply it? (props first: it is the live truth)
    props_ctx = None
    props_slots = None
    if props:
        gen = props.get("default_generation_settings") or {}
        props_ctx = gen.get("n_ctx")
        props_slots = props.get("total_slots")

    if config.get("ctx_size") not in (None, ""):
        if props_ctx is not None:
            # /props reports the per-slot context; with N slots the total is N x
            checks.append(_compare_number("ctx_size -> server", config["ctx_size"], _ctx_total(props_ctx, props_slots), "live /props"))
        else:
            total = observed["ctx_total"] or (
                observed["ctx_per_slot"] * (observed["slots"] or 1) if observed["ctx_per_slot"] else None
            )
            checks.append(_compare_number("ctx_size -> server", config["ctx_size"], total, "startup log"))
    if config.get("threads") not in (None, ""):
        checks.append(_compare_number("threads -> server", config["threads"], observed["threads"], "startup log"))
    if config.get("parallel_slots") not in (None, ""):
        actual = props_slots if props_slots is not None else observed["slots"]
        checks.append(_compare_number("parallel_slots -> server", config["parallel_slots"], actual, "live /props" if props_slots is not None else "startup log"))

    # 3) the one that matters most: is the model on the GPU?
    warnings: list[str] = []
    asked = config.get("n_gpu_layers")
    wants_gpu = _wants_gpu(asked)
    if wants_gpu:
        if place == CPU:
            checks.append(_check("model on GPU", MISMATCH, str(asked), "CPU", "0 layers offloaded"))
            warnings.append(_cpu_warning(asked, devices))
        elif place == PARTIAL:
            checks.append(
                _check(
                    "model on GPU",
                    MISMATCH,
                    str(asked),
                    f"{observed['layers_offloaded']}/{observed['layers_total']} layers",
                    "only part of the model fits in VRAM; the rest runs on the CPU (much slower)",
                )
            )
            warnings.append("فقط بخشی از لایه‌ها روی GPU رفته؛ بقیه روی CPU اجرا می‌شود. Context یا کوانتیزیشن را کوچک‌تر کن.")
        elif place == GPU:
            checks.append(_check("model on GPU", OK, str(asked), f"{observed['layers_offloaded']}/{observed['layers_total']} layers", ""))
        else:
            checks.append(_check("model on GPU", UNKNOWN, str(asked), None, "the log has no offload line (older/quiet build, or the log buffer was cut)"))
            if devices is not None and devices.get("probed") and not devices.get("gpus"):
                warnings.append(_cpu_warning(asked, devices))
    if observed["kv_mib"]["cpu"] > 0 and observed["kv_mib"]["gpu"] == 0 and place in (GPU, PARTIAL):
        warnings.append("KV cache روی RAM است (نه GPU)؛ تولید جواب کند می‌شود. گزینه‌ی KV offload را روی خالی/on بگذار.")

    return {
        "launch_command": command,
        "observed": observed,
        "props": {"n_ctx_per_slot": props_ctx, "total_slots": props_slots} if props else None,
        "placement": place,
        "checks": checks,
        "warnings": warnings,
        "devices": devices,
        "ok": not warnings and all(c["status"] != MISMATCH for c in checks),
    }


def _ctx_total(per_slot, slots):
    try:
        return int(per_slot) * int(slots or 1)
    except (TypeError, ValueError):
        return per_slot


def _wants_gpu(value) -> bool:
    if value in (None, "", 0, "0"):
        return False
    return True  # a number > 0, "all", "auto"


def _cpu_warning(asked, devices: Optional[dict]) -> str:
    base = f"تنظیم GPU Layers = {asked} است، ولی مدل روی CPU اجرا می‌شود."
    if devices is not None and devices.get("probed") and not devices.get("gpus"):
        return base + " این llama-server هیچ دستگاه GPU ای نمی‌بیند (--list-devices خالی است): نسخه‌ی CUDA/Vulkan آن را نصب کن."
    return base + " احتمالاً این build فقط CPU دارد؛ خروجی «llama-server --list-devices» را بررسی کن."
