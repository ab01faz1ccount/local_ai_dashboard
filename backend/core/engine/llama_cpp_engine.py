"""
backend/core/engine/llama_cpp_engine.py

LlamaCppEngine: concrete InferenceEngine for llama.cpp's `llama-server`.
Talks to the OS only through an injected PlatformProvider -- this file has
zero platform.system() branching. A future vLLM/Ollama engine would look
almost identical except for _build_args() and the health-check URL.
"""

from __future__ import annotations

import json
import time
from typing import Optional
from urllib import request as urlrequest
from urllib.error import URLError

from ..platform.base import PlatformProvider
from .base import EngineMetrics, EngineStatus, InferenceEngine, RuntimeState


class LlamaCppEngine(InferenceEngine):
    def __init__(self, platform_provider: PlatformProvider, executable_path: str):
        self._platform = platform_provider
        self._executable_path = executable_path
        self._pid: Optional[int] = None
        self._config: dict = {}
        self._state: RuntimeState = RuntimeState.OFFLINE
        self._error_message: Optional[str] = None
        self._started_at: Optional[float] = None

    # -- lifecycle --------------------------------------------------------

    def start(self, config: dict) -> EngineStatus:
        current = self.get_status()
        if current.state == RuntimeState.ONLINE:
            return current  # idempotent: don't spawn a second process

        self._config = config
        self._state = RuntimeState.STARTING
        self._error_message = None

        args = self._build_args(config)
        try:
            self._pid = self._platform.start_process(self._executable_path, args)
        except Exception as exc:
            self._state = RuntimeState.ERROR
            self._error_message = f"failed to launch process: {exc}"
            return self.get_status()

        self._started_at = time.time()

        if self._wait_for_health(timeout=config.get("startup_timeout_seconds", 30)):
            self._state = RuntimeState.ONLINE
        else:
            self._state = RuntimeState.ERROR
            self._error_message = "process started but health check never succeeded"

        return self.get_status()

    def stop(self, timeout: float = 10.0) -> EngineStatus:
        if self._pid is None:
            self._state = RuntimeState.OFFLINE
            return self.get_status()

        self._state = RuntimeState.STOPPING
        stopped = self._platform.stop_process(self._pid, timeout=timeout)
        self._state = RuntimeState.OFFLINE if stopped else RuntimeState.ERROR
        if not stopped:
            self._error_message = "process did not stop within timeout"
        self._pid = None
        self._started_at = None
        return self.get_status()

    def restart(self, config: Optional[dict] = None) -> EngineStatus:
        self.stop()
        return self.start(config if config is not None else self._config)

    def get_status(self) -> EngineStatus:
        if self._pid is not None and not self._platform.is_process_running(self._pid):
            # Process died on its own (crash) -- surface it as ERROR here
            # so every caller sees an accurate state on the next poll,
            # rather than needing its own crash-detection logic.
            self._state = RuntimeState.ERROR
            self._error_message = self._error_message or "process exited unexpectedly"
            self._pid = None

        uptime = (time.time() - self._started_at) if self._started_at else None
        host = self._config.get("host", "127.0.0.1")
        port = self._config.get("port")
        endpoint = f"http://{host}:{port}" if port else None

        return EngineStatus(
            state=self._state,
            pid=self._pid,
            endpoint=endpoint,
            uptime_seconds=uptime,
            error_message=self._error_message,
        )

    # -- metrics / logs -----------------------------------------------------

    def get_metrics(self) -> EngineMetrics:
        """Pulls llama-server's own `/slots` endpoint when reachable.
        Never raises -- an unreachable/older server just yields an empty
        EngineMetrics instead of breaking the polling loop."""
        status = self.get_status()
        if status.state != RuntimeState.ONLINE or not status.endpoint:
            return EngineMetrics()

        try:
            with urlrequest.urlopen(f"{status.endpoint}/slots", timeout=2) as resp:
                slots = json.loads(resp.read())
        except (URLError, TimeoutError, ValueError, OSError):
            return EngineMetrics()

        active = [s for s in slots if s.get("state") in (1, "processing")]
        tokens_per_sec = None
        if slots:
            last = slots[-1]
            tokens_per_sec = last.get("tokens_predicted_per_second") or (
                last.get("generation_settings", {}) or {}
            ).get("tokens_per_second")

        return EngineMetrics(
            tokens_per_sec=tokens_per_sec,
            last_latency_ms=None,
            active_slots=len(active),
            total_requests=None,
            extra={"raw_slots": slots},
        )

    def tail_logs(self, n: int = 200) -> list[str]:
        if self._pid is None:
            return []
        return self._platform.get_process_logs(self._pid, n)

    # -- internals ------------------------------------------------------

    def _build_args(self, config: dict) -> list[str]:
        """Translates our config dict into llama-server CLI flags. Covers
        the commonly-tuned flags explicitly; anything else can still be
        passed via config["extra_args"] (list[str]) so the full surface of
        llama.cpp flags is reachable without this list growing forever."""
        args: list[str] = []

        def add(flag: str, key: str) -> None:
            if key in config and config[key] is not None:
                args.extend([flag, str(config[key])])

        if "model_path" in config:
            args.extend(["--model", str(config["model_path"])])

        add("--host", "host")
        add("--port", "port")
        add("--ctx-size", "ctx_size")
        add("--n-gpu-layers", "n_gpu_layers")
        add("--threads", "threads")
        add("--batch-size", "batch_size")
        add("--ubatch-size", "ubatch_size")
        add("--parallel", "parallel_slots")
        add("--temp", "temperature")
        add("--top-k", "top_k")
        add("--top-p", "top_p")
        add("--min-p", "min_p")
        add("--repeat-penalty", "repeat_penalty")
        add("--presence-penalty", "presence_penalty")
        add("--frequency-penalty", "frequency_penalty")
        add("--seed", "seed")

        # KV cache: type (quantized KV cache trades memory for quality/
        # stability, per llama.cpp's own docs) and whether it's offloaded
        # to the GPU at all.
        add("--cache-type-k", "cache_type_k")
        add("--cache-type-v", "cache_type_v")
        if "kv_offload" in config and config["kv_offload"] is not None:
            args.append("--kv-offload" if config["kv_offload"] else "--no-kv-offload")

        # RoPE / YaRN -- leave the model's own defaults alone unless the
        # user explicitly set one of these (matches upstream's own
        # guidance: don't touch without a reason).
        add("--rope-scaling", "rope_scaling")
        add("--rope-scale", "rope_scale")
        add("--rope-freq-base", "rope_freq_base")
        add("--rope-freq-scale", "rope_freq_scale")
        add("--yarn-orig-ctx", "yarn_orig_ctx")
        add("--yarn-ext-factor", "yarn_ext_factor")
        add("--yarn-attn-factor", "yarn_attn_factor")
        add("--yarn-beta-fast", "yarn_beta_fast")
        add("--yarn-beta-slow", "yarn_beta_slow")

        add("--chat-template", "chat_template")
        add("--numa", "numa")
        add("--device", "device")
        add("--tensor-split", "tensor_split")

        if config.get("embedding"):
            args.append("--embedding")
        if config.get("flash_attn"):
            args.append("--flash-attn")
        if config.get("mlock"):
            args.append("--mlock")
        if config.get("no_mmap"):
            args.append("--no-mmap")

        args.extend(config.get("extra_args", []))
        return args

    def _wait_for_health(self, timeout: float) -> bool:
        host = self._config.get("host", "127.0.0.1")
        port = self._config.get("port")
        if not port:
            return False
        url = f"http://{host}:{port}/health"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urlrequest.urlopen(url, timeout=2) as resp:
                    if resp.status == 200:
                        return True
            except (URLError, TimeoutError, OSError):
                pass
            time.sleep(0.5)
        return False
