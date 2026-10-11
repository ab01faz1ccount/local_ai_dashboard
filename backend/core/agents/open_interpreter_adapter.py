"""
backend/core/agents/open_interpreter_adapter.py

Open Interpreter (github.com/openinterpreter/openinterpreter), driven through
its real terminal UI -- the same way Hermes is.

How its configuration works (from its own docs: /docs/terminal/config and
/docs/terminal/providers -- NOT yet checked against a real install):
  * config file: ~/.openinterpreter/config.toml, TOML
  * `model` / `model_provider` choose the model and the provider id
  * a custom provider is a `[model_providers.<id>]` table with `base_url`,
    `env_key` (the NAME of an environment variable holding the key -- the key
    itself is never stored in the file) and `wire_api` ("chat" for an
    OpenAI-compatible Chat Completions server such as llama-server)
  * there is no `config set` command, so this adapter edits the file itself,
    keeps every other key, and saves a one-time backup first.

Unlike Hermes there is no CLI to read a value back, so verification parses the
file again. The API key travels in the process environment (`launch_env`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from . import paths, toml_edit
from .base import AgentAdapter, AgentBackendInfo, register_adapter

TIMEOUT_SECONDS = 15
PROVIDER_ID = "local_llama"
API_KEY_ENV = "LOCAL_LLAMA_API_KEY"
LOCAL_MODEL_PLACEHOLDER = "local-model"
BACKUP_SUFFIX = ".bak-before-local-ai"


class OpenInterpreterAdapter(AgentAdapter):
    backend_id = "open_interpreter"
    display_name = "Open Interpreter"
    brand_color = "#7c8cff"
    sensitive_settings = frozenset()  # the key lives in the environment, not the file

    def _executable(self) -> Optional[str]:
        return paths.get_path(self.backend_id) or shutil.which("interpreter") or shutil.which("i")

    def _config_path(self) -> Path:
        return Path.home() / ".openinterpreter" / "config.toml"

    def detect(self) -> AgentBackendInfo:
        path = self._executable()
        if not path:
            return AgentBackendInfo(self.backend_id, self.display_name, self.brand_color, detected=False)
        version = None
        try:
            result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
            version = (result.stdout or result.stderr).strip() or None
        except (OSError, subprocess.TimeoutExpired):
            pass
        return AgentBackendInfo(self.backend_id, self.display_name, self.brand_color, True, path, version)

    def expected_settings(
        self,
        runtime_base_url: str,
        api_key: str,
        model_name: Optional[str] = None,
        context_length: Optional[int] = None,
    ) -> dict[str, str]:
        # context_length: no documented key -> deliberately not written.
        p = f"model_providers.{PROVIDER_ID}"
        return {
            "model_provider": PROVIDER_ID,
            "model": model_name or LOCAL_MODEL_PLACEHOLDER,
            f"{p}.name": "Local llama-server",
            f"{p}.base_url": runtime_base_url,
            f"{p}.env_key": API_KEY_ENV,
            f"{p}.wire_api": "chat",
        }

    def _load(self) -> dict:
        path = self._config_path()
        if not path.exists():
            return {}
        try:
            return toml_edit.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"{path} خوانده نشد: {exc}") from exc

    def read_setting(self, key: str) -> Optional[str]:
        value = toml_edit.get_dotted(self._load(), key)
        return None if value is None else str(value)

    def configure(
        self,
        runtime_base_url: str,
        api_key: str,
        model_name: Optional[str] = None,
        context_length: Optional[int] = None,
    ) -> list[str]:
        if not self._executable():
            raise RuntimeError(
                "Open Interpreter پیدا نشد (نه روی PATH هست، نه مسیری براش انتخاب شده) — اول نصبش کن یا از Settings → Browse مسیرش رو بده."
            )
        path = self._config_path()
        data = self._load()
        for key, value in self.expected_settings(runtime_base_url, api_key, model_name, context_length).items():
            toml_edit.set_dotted(data, key, value)
        try:
            text = toml_edit.dumps(data)
        except toml_edit.UnsupportedToml as exc:
            raise RuntimeError(
                f"{path} ساختاری دارد که بدون خطر بازنویسی نمی‌شود ({exc}) — دست نخورده ماند. "
                f"این مقادیر را خودت در آن بگذار: {self.expected_settings(runtime_base_url, api_key, model_name)}"
            ) from exc

        path.parent.mkdir(parents=True, exist_ok=True)
        ran: list[str] = []
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if path.exists() and not backup.exists():
            shutil.copy2(path, backup)  # once: the user's original, never overwritten
            ran.append(f"backup -> {backup}")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)  # atomic: never leave a half-written config
        ran.append(f"write {path}")
        return ran

    def launch_env(self, api_key: str = "sk-no-key", **_endpoint) -> dict[str, str]:
        return {API_KEY_ENV: api_key}

    def build_launch_command(self, resume_session_id: Optional[str] = None) -> list[str]:
        exe = self._executable()
        if not exe:
            raise RuntimeError("Open Interpreter پیدا نشد — نمی‌شه ترمینالش رو باز کرد.")
        return [exe]


register_adapter(OpenInterpreterAdapter())
