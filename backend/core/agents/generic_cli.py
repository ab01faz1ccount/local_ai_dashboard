"""
backend/core/agents/generic_cli.py

An AgentAdapter described by DATA instead of code, so any agent CLI can be
connected to a local runtime without a new Python module -- the point of this
app being generic across agents.

A "spec" says three things about a CLI:
  1. how to find and run it             (executable, version_args, launch_args)
  2. where its provider settings live   (config.mode)
       env_only   nothing is written; the endpoint travels in environment variables
       cli_set    the CLI has a `config set` style command (run once per setting)
       toml_file  the CLI reads a TOML file (edited in place, backed up once)
  3. which settings/variables get which value, as templates over
       {base_url} {api_key} {model} {context}

Example (a CLI that reads the usual OpenAI variables):
    {"name": "My agent", "executable": "myagent",
     "config": {"mode": "env_only"},
     "env": {"OPENAI_BASE_URL": "{base_url}", "OPENAI_API_KEY": "{api_key}", "OPENAI_MODEL": "{model}"}}

Safety: commands are argv lists, never a shell string; `set_command`/`get_command`
must start with `{exe}` (the spec's own executable) so a spec can't turn this into
"run any program"; only the placeholders above are allowed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from . import toml_edit
from .base import AgentAdapter, AgentBackendInfo

BACKEND_ID = "custom_cli"
TIMEOUT_SECONDS = 15
BACKUP_SUFFIX = ".bak-before-local-ai"
LOCAL_MODEL_PLACEHOLDER = "local-model"
MODES = ("env_only", "cli_set", "toml_file")

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_VALUE_FIELDS = {"base_url", "api_key", "model", "context"}
_SET_FIELDS = {"exe", "key", "value"}
_GET_FIELDS = {"exe", "key"}
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Starting points, not claims about any specific tool: the user picks one and edits it.
PRESETS: dict[str, dict] = {
    "openai_env": {
        "label": "Reads the standard OpenAI environment variables",
        "config": {"mode": "env_only"},
        "env": {
            "OPENAI_BASE_URL": "{base_url}",
            "OPENAI_API_BASE": "{base_url}",
            "OPENAI_API_KEY": "{api_key}",
            "OPENAI_MODEL": "{model}",
        },
    },
    "cli_config_set": {
        "label": "Has a `<cli> config set <key> <value>` command",
        "config": {
            "mode": "cli_set",
            "set_command": ["{exe}", "config", "set", "{key}", "{value}"],
            "get_command": ["{exe}", "config", "get", "{key}"],
        },
        "settings": {"model.base_url": "{base_url}", "model.name": "{model}", "model.api_key": "{api_key}"},
    },
    "toml_provider": {
        "label": "Keeps its provider in a TOML config file",
        "config": {"mode": "toml_file", "path": "~/.mycli/config.toml"},
        "settings": {"base_url": "{base_url}", "model": "{model}"},
    },
}


class SpecError(ValueError):
    """The spec is malformed; the message says which field and why."""


def _fields_in(template: str) -> set[str]:
    return set(_PLACEHOLDER.findall(template))


def _check_templates(where: str, templates, allowed: set[str]) -> None:
    for t in templates:
        if not isinstance(t, str):
            raise SpecError(f"{where}: every value must be text.")
        bad = _fields_in(t) - allowed
        if bad:
            raise SpecError(f"{where}: unknown placeholder {{{sorted(bad)[0]}}} (allowed: {', '.join(sorted(allowed))}).")


def validate_spec(spec) -> dict:
    """Returns a normalized copy of `spec` or raises SpecError."""
    if not isinstance(spec, dict):
        raise SpecError("The spec must be an object.")
    name = str(spec.get("name") or "").strip()
    exe = str(spec.get("executable") or "").strip()
    if not name:
        raise SpecError("name is required.")
    if not exe:
        raise SpecError("executable is required (a command name on PATH, or a full path).")
    if any(c in exe for c in ("\n", "\r", "\0")) or (" " in exe and not os.path.isabs(exe)):
        raise SpecError("executable must be one command name or one path, not a command line (put flags in launch_args).")

    cfg = spec.get("config") or {}
    mode = cfg.get("mode", "env_only")
    if mode not in MODES:
        raise SpecError(f"config.mode must be one of {', '.join(MODES)}.")

    settings = spec.get("settings") or {}
    env = spec.get("env") or {}
    if not isinstance(settings, dict) or not isinstance(env, dict):
        raise SpecError("settings and env must be objects.")
    _check_templates("settings", settings.values(), _VALUE_FIELDS)
    _check_templates("env", env.values(), _VALUE_FIELDS)
    for k in env:
        if not _ENV_NAME.match(str(k)):
            raise SpecError(f"env: '{k}' is not a valid variable name.")
    for k in settings:
        if not str(k).strip():
            raise SpecError("settings: keys can't be empty.")

    out_cfg: dict = {"mode": mode}
    if mode == "cli_set":
        set_cmd = cfg.get("set_command")
        if not isinstance(set_cmd, list) or not set_cmd or set_cmd[0] != "{exe}":
            raise SpecError("config.set_command must be a list starting with \"{exe}\".")
        _check_templates("config.set_command", set_cmd, _SET_FIELDS)
        out_cfg["set_command"] = list(set_cmd)
        get_cmd = cfg.get("get_command")
        if get_cmd is not None:
            if not isinstance(get_cmd, list) or not get_cmd or get_cmd[0] != "{exe}":
                raise SpecError("config.get_command must be a list starting with \"{exe}\".")
            _check_templates("config.get_command", get_cmd, _GET_FIELDS)
            out_cfg["get_command"] = list(get_cmd)
        if not settings:
            raise SpecError("cli_set needs at least one entry in settings.")
    elif mode == "toml_file":
        path = str(cfg.get("path") or "").strip()
        if not path:
            raise SpecError("config.path is required for toml_file.")
        out_cfg["path"] = path
        if not settings:
            raise SpecError("toml_file needs at least one entry in settings.")
    elif not env:
        raise SpecError("env_only needs at least one entry in env.")

    for key in ("version_args", "launch_args", "sensitive"):
        v = spec.get(key)
        if v is not None and (not isinstance(v, list) or not all(isinstance(i, str) for i in v)):
            raise SpecError(f"{key} must be a list of text.")

    return {
        "name": name,
        "executable": exe,
        "version_args": list(spec.get("version_args") or ["--version"]),
        "launch_args": list(spec.get("launch_args") or []),
        "config": out_cfg,
        "settings": {str(k): str(v) for k, v in settings.items()},
        "env": {str(k): str(v) for k, v in env.items()},
        "sensitive": list(spec.get("sensitive") or []),
    }


def _render(template: str, values: dict[str, Optional[str]]) -> Optional[str]:
    """Fills placeholders; None if one has no value (so that entry is skipped
    instead of writing the literal text "{context}" into someone's config)."""
    missing = False

    def sub(m: re.Match) -> str:
        nonlocal missing
        v = values.get(m.group(1))
        if v is None:
            missing = True
            return ""
        return v

    out = _PLACEHOLDER.sub(sub, template)
    return None if missing else out


class GenericCliAdapter(AgentAdapter):
    backend_id = BACKEND_ID
    brand_color = "#8a8f98"

    def __init__(self, spec: dict):
        self.spec = validate_spec(spec)
        self.display_name = self.spec["name"]
        self.sensitive_settings = frozenset(self.spec["sensitive"]) | frozenset(
            k for k, t in self.spec["settings"].items() if "api_key" in _fields_in(t)
        )

    # -- lookup --------------------------------------------------------
    def _executable(self) -> Optional[str]:
        exe = os.path.expanduser(self.spec["executable"])
        if os.path.isabs(exe):
            return exe if os.path.isfile(exe) else None
        return shutil.which(exe)

    def detect(self) -> AgentBackendInfo:
        path = self._executable()
        if not path:
            return AgentBackendInfo(self.backend_id, self.display_name, self.brand_color, detected=False)
        version = None
        try:
            r = subprocess.run([path, *self.spec["version_args"]], capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
            version = (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else None
        except (OSError, subprocess.TimeoutExpired):
            pass
        return AgentBackendInfo(self.backend_id, self.display_name, self.brand_color, True, path, version)

    # -- values --------------------------------------------------------
    def _values(self, base_url: str, api_key: str, model_name: Optional[str], context_length: Optional[int]) -> dict:
        return {
            "base_url": base_url,
            "api_key": api_key,
            "model": model_name or LOCAL_MODEL_PLACEHOLDER,
            "context": str(int(context_length)) if context_length else None,
        }

    def expected_settings(self, runtime_base_url, api_key, model_name=None, context_length=None) -> dict[str, str]:
        vals = self._values(runtime_base_url, api_key, model_name, context_length)
        out = {}
        for key, tmpl in self.spec["settings"].items():
            rendered = _render(tmpl, vals)
            if rendered is not None:
                out[key] = rendered
        return out

    def launch_env(self, api_key: str = "sk-no-key", *, base_url: Optional[str] = None,
                   model_name: Optional[str] = None, context_length: Optional[int] = None, **_ignored) -> dict[str, str]:
        if base_url is None:
            return {}
        vals = self._values(base_url, api_key, model_name, context_length)
        out = {}
        for name, tmpl in self.spec["env"].items():
            rendered = _render(tmpl, vals)
            if rendered is not None:
                out[name] = rendered
        return out

    # -- configure / read back ------------------------------------------
    def _toml_path(self) -> Path:
        return Path(os.path.expanduser(self.spec["config"]["path"]))

    def _load_toml(self) -> dict:
        path = self._toml_path()
        if not path.exists():
            return {}
        try:
            return toml_edit.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"{path}: could not be read ({exc})") from exc

    def read_setting(self, key: str) -> Optional[str]:
        mode = self.spec["config"]["mode"]
        if mode == "toml_file":
            value = toml_edit.get_dotted(self._load_toml(), key)
            return None if value is None else str(value)
        if mode == "cli_set":
            get_cmd = self.spec["config"].get("get_command")
            exe = self._executable()
            if not get_cmd or not exe:
                raise RuntimeError("this CLI has no get_command, so values can't be read back")
            argv = [exe if a == "{exe}" else a.replace("{key}", key) for a in get_cmd]
            try:
                r = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError(f"`{' '.join(argv)}` failed: {exc}") from exc
            if r.returncode != 0:
                raise RuntimeError(f"`{' '.join(argv)}` failed: {(r.stderr or r.stdout).strip()}")
            return r.stdout.strip()
        raise RuntimeError("env_only: nothing is stored, the endpoint is passed in the environment")

    def configure(self, runtime_base_url, api_key, model_name=None, context_length=None) -> list[str]:
        mode = self.spec["config"]["mode"]
        if mode == "env_only":
            return []  # nothing to write; launch_env() carries everything
        settings = self.expected_settings(runtime_base_url, api_key, model_name, context_length)

        if mode == "cli_set":
            exe = self._executable()  # only a `config set` CLI has to be runnable to be configured
            if not exe:
                raise RuntimeError(f"{self.display_name}: '{self.spec['executable']}' was not found on PATH or at that path.")
            ran: list[str] = []
            for key, value in settings.items():
                argv = [exe if a == "{exe}" else a.replace("{key}", key).replace("{value}", value)
                        for a in self.spec["config"]["set_command"]]
                try:
                    r = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise RuntimeError(f"`{' '.join(argv)}` failed: {exc}") from exc
                if r.returncode != 0:
                    raise RuntimeError(f"`{' '.join(argv)}` failed: {(r.stderr or r.stdout).strip()}")
                shown = " ".join("***" if (a == value and key in self.sensitive_settings) else a for a in argv)
                ran.append(shown)
            return ran

        # toml_file
        path = self._toml_path()
        data = self._load_toml()
        for key, value in settings.items():
            toml_edit.set_dotted(data, key, value)
        try:
            text = toml_edit.dumps(data)
        except toml_edit.UnsupportedToml as exc:
            raise RuntimeError(f"{path} has structure that can't be rewritten safely ({exc}); it was left untouched.") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        ran = []
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if path.exists() and not backup.exists():
            shutil.copy2(path, backup)
            ran.append(f"backup -> {backup}")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        ran.append(f"write {path}")
        return ran

    def build_launch_command(self, resume_session_id: Optional[str] = None) -> list[str]:
        exe = self._executable()
        if not exe:
            raise RuntimeError(f"{self.display_name}: '{self.spec['executable']}' was not found, so its terminal can't be opened.")
        return [exe, *self.spec["launch_args"]]
