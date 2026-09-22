"""
backend/core/agents/hermes_adapter.py

First concrete AgentAdapter, built directly from the user's own working
recipe for wiring Hermes Agent (Nous Research) to a local llama-server:

    hermes config set model.provider custom
    hermes config set model.base_url http://127.0.0.1:PORT/v1
    hermes config set LLAMA_CPP_API_KEY sk-no-key
    hermes config set model.name <name>        (optional)

`--jinja` on the llama-server side (required for Hermes's tool calling)
is NOT this adapter's concern -- that's a runtime launch flag, already
covered by the Settings panel's llama.cpp config (chat_template /
extra_args), not something the agent side configures.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Optional

from . import paths
from .base import AgentAdapter, AgentBackendInfo, register_adapter

HERMES_TIMEOUT_SECONDS = 15


class HermesAdapter(AgentAdapter):
    backend_id = "hermes"
    display_name = "Hermes Agent"
    # Amber/orange, matching Hermes's own CLI banner (see the reference
    # screenshot) -- used only to theme our terminal panel's chrome; the
    # actual banner/colors the user sees come from the real process.
    brand_color = "#f5a623"

    def _executable(self) -> Optional[str]:
        # A path the user picked through the offline browser wins over
        # PATH lookup -- it's what lets a hermes install that isn't on
        # PATH still work. Every command below runs this resolved path
        # rather than the bare name "hermes" for the same reason.
        return paths.get_path(self.backend_id) or shutil.which("hermes")

    def detect(self) -> AgentBackendInfo:
        path = self._executable()
        if not path:
            return AgentBackendInfo(self.backend_id, self.display_name, self.brand_color, detected=False)

        version = None
        try:
            result = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=HERMES_TIMEOUT_SECONDS
            )
            version = (result.stdout or result.stderr).strip() or None
        except (OSError, subprocess.TimeoutExpired):
            pass

        return AgentBackendInfo(self.backend_id, self.display_name, self.brand_color, True, path, version)

    def configure(self, runtime_base_url: str, api_key: str, model_name: Optional[str] = None) -> list[str]:
        exe = self._executable()
        if not exe:
            raise RuntimeError(
                "hermes CLI پیدا نشد (نه روی PATH هست، نه مسیری براش انتخاب شده) — اول نصبش کن یا از Settings → Browse مسیرش رو بده."
            )

        commands = [
            [exe, "config", "set", "model.provider", "custom"],
            [exe, "config", "set", "model.base_url", runtime_base_url],
            [exe, "config", "set", "LLAMA_CPP_API_KEY", api_key],
        ]
        if model_name:
            commands.append([exe, "config", "set", "model.name", model_name])

        ran: list[str] = []
        for cmd in commands:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=HERMES_TIMEOUT_SECONDS)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError(f"`{' '.join(cmd)}` failed: {exc}") from exc
            if result.returncode != 0:
                raise RuntimeError(f"`{' '.join(cmd)}` failed: {(result.stderr or result.stdout).strip()}")
            ran.append(" ".join(cmd))
        return ran

    def build_launch_command(self, resume_session_id: Optional[str] = None) -> list[str]:
        exe = self._executable()
        if not exe:
            raise RuntimeError("hermes CLI پیدا نشد — نمی‌شه ترمینالش رو باز کرد.")
        if resume_session_id:
            return [exe, "-r", resume_session_id]
        return [exe]


register_adapter(HermesAdapter())
