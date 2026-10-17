"""
backend/core/agents/mcp_sync.py

Hand the app's MCP servers to an EXTERNAL agent (Hermes, Open Interpreter,
or any CLI that keeps MCP servers in a config file) by writing them into
that agent's own config -- then read the file back and compare.

Why this exists: the app's own chat loop already lets a model call MCP tools
(core/agent_loop.py). An external agent is a separate process that reads its
own config; it never sees the app's servers unless they are written there.

Rules this module keeps:
  * Only entries it owns are ever touched. Ours are named `lai_<server>`;
    anything else in the file (the user's own servers, comments outside the
    mcp block, every other setting) is left alone. A file we can't rewrite
    faithfully is left untouched and reported.
  * The user's original file is copied to `<name>.lai-backup` once.
  * Secrets (env values, header values) are only written when the caller
    says so explicitly -- they end up in plain text in the agent's file.
    Reports never carry those values, only key names.
  * Verification is a read-back of the file we wrote, and (optional) a probe
    through the agent's own CLI. "In the file" is not "the agent connected";
    the report says which of the two was proven.
  * The app's Permission Engine does NOT apply to an external agent: it runs
    the tools itself. What carries over is the tool on/off list (disabled
    tools are excluded where the agent supports that).
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import toml_edit

PREFIX = "lai_"
BACKUP_SUFFIX = ".lai-backup"
SUPPORTED_TRANSPORTS = ("stdio", "http")


# -- what we want to hand over ---------------------------------------------

@dataclass
class McpEntry:
    """One app server, already resolved to plain values."""

    name: str  # the app's server name
    transport: str  # stdio | http | sse
    command: Optional[str] = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    disabled_tools: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return agent_key(self.name)

    @property
    def has_secrets(self) -> bool:
        return bool(self.env or self.headers)


def agent_key(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "server"
    return PREFIX + slug


# -- per-agent field names --------------------------------------------------

@dataclass(frozen=True)
class McpProfile:
    fmt: str  # "yaml" | "toml"
    container: str = "mcp_servers"
    headers_field: str = "headers"
    # how disabled tools are written: ("flat", "disabled_tools") ->
    # {"disabled_tools": [...]}; ("nested", ("tools", "exclude")) -> {"tools": {"exclude": [...]}}
    tools_path: Optional[tuple[str, ...]] = None


HERMES_PROFILE = McpProfile("yaml", tools_path=("tools", "exclude"))
OPEN_INTERPRETER_PROFILE = McpProfile("toml", headers_field="http_headers", tools_path=("disabled_tools",))


def native_entry(entry: McpEntry, profile: McpProfile) -> dict:
    """The dict the agent's config holds for this server."""
    out: dict[str, Any] = {}
    if entry.transport == "stdio":
        out["command"] = entry.command or ""
        if entry.args:
            out["args"] = list(entry.args)
        if entry.env:
            out["env"] = dict(entry.env)
    else:
        out["url"] = entry.url or ""
        if entry.headers:
            out[profile.headers_field] = dict(entry.headers)
    if entry.disabled_tools and profile.tools_path:
        cur = out
        for part in profile.tools_path[:-1]:
            cur = cur.setdefault(part, {})
        cur[profile.tools_path[-1]] = sorted(entry.disabled_tools)
    return out


def _flatten(d: dict, prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, dict):
            flat.update(_flatten(v, f"{prefix}{k}."))
        else:
            flat[f"{prefix}{k}"] = v
    return flat


def differing_fields(want: dict, have: dict) -> list[str]:
    """Names of the fields that differ. Values are never returned (secrets)."""
    a, b = _flatten(want), _flatten(have)
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


# -- file stores ------------------------------------------------------------

class McpSyncError(RuntimeError):
    pass


class McpStore:
    """Reads / writes the `container` mapping of one agent config file."""

    def __init__(self, path: Path, profile: McpProfile):
        self.path = Path(path)
        self.profile = profile

    # -- reading
    def _text(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        except OSError as exc:
            raise McpSyncError(f"{self.path}: could not be read ({exc})") from exc

    def _parse(self, text: str) -> dict:
        if self.profile.fmt == "toml":
            try:
                return toml_edit.loads(text) if text.strip() else {}
            except ValueError as exc:
                raise McpSyncError(f"{self.path}: not valid TOML ({exc})") from exc
        yaml = _yaml()
        try:
            data = yaml.safe_load(text) if text.strip() else {}
        except yaml.YAMLError as exc:
            raise McpSyncError(f"{self.path}: not valid YAML ({exc})") from exc
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise McpSyncError(f"{self.path}: top level is not a mapping")
        return data

    def read_all(self) -> dict[str, dict]:
        block = self._parse(self._text()).get(self.profile.container) or {}
        if not isinstance(block, dict):
            raise McpSyncError(f"{self.path}: `{self.profile.container}` is not a table")
        return {str(k): v for k, v in block.items() if isinstance(v, dict)}

    def read_managed(self) -> dict[str, dict]:
        return {k: v for k, v in self.read_all().items() if k.startswith(PREFIX)}

    # -- writing
    def write_managed(self, desired: dict[str, dict]) -> list[str]:
        """Make the file's `lai_*` entries exactly `desired`; touch nothing else."""
        text = self._text()
        data = self._parse(text)
        block = data.get(self.profile.container) or {}
        if not isinstance(block, dict):
            raise McpSyncError(f"{self.path}: `{self.profile.container}` is not a table")
        merged = {k: v for k, v in block.items() if not str(k).startswith(PREFIX)}
        merged.update(desired)
        if self.profile.fmt == "toml":
            new_text = self._render_toml(data, merged)
        else:
            new_text = self._render_yaml(text, data, merged)
        if new_text == text:
            return []
        ran: list[str] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        backup = self.path.with_name(self.path.name + BACKUP_SUFFIX)
        if self.path.exists() and not backup.exists():
            shutil.copy2(self.path, backup)
            ran.append(f"backup -> {backup}")
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, self.path)
        ran.append(f"write {self.path}")
        return ran

    def _render_toml(self, data: dict, merged: dict) -> str:
        new = dict(data)
        if merged:
            new[self.profile.container] = merged
        else:
            new.pop(self.profile.container, None)
        try:
            return toml_edit.dumps(new)
        except toml_edit.UnsupportedToml as exc:
            raise McpSyncError(
                f"{self.path} has a structure that can't be rewritten without risk ({exc}); left untouched."
            ) from exc

    def _render_yaml(self, text: str, data: dict, merged: dict) -> str:
        yaml = _yaml()
        key = self.profile.container
        block_text = ""
        if merged:
            block_text = yaml.safe_dump({key: merged}, sort_keys=False, allow_unicode=True, default_flow_style=False)
        lines = text.splitlines()
        start = next((i for i, ln in enumerate(lines) if re.match(rf"^{re.escape(key)}\s*:", ln)), None)
        if start is None:
            new_text = text if not text or text.endswith("\n") else text + "\n"
            new_text += block_text
        else:
            end = start + 1
            # the block = following indented lines (blank lines between them
            # included); a column-0 line, comment or not, belongs to what follows
            while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t"):
                end += 1
            while end > start + 1 and not lines[end - 1].strip():
                end -= 1  # blank lines after the block stay where they were
            new_text = "\n".join(lines[:start] + block_text.splitlines() + lines[end:])
            new_text = new_text + "\n" if new_text.strip() else ""
        # Every OTHER setting must parse to exactly what it was before.
        try:
            after = yaml.safe_load(new_text) if new_text.strip() else {}
        except yaml.YAMLError as exc:
            raise McpSyncError(f"{self.path}: rewriting would break the file ({exc}); left untouched.") from exc
        after = after or {}
        before_rest = {k: v for k, v in data.items() if k != key}
        after_rest = {k: v for k, v in after.items() if k != key}
        if before_rest != after_rest or (after.get(key) or {}) != merged:
            raise McpSyncError(f"{self.path}: rewriting would change other settings; left untouched.")
        return new_text


def _yaml():
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise McpSyncError("PyYAML is needed for this agent's config (pip install pyyaml).") from exc
    return yaml


# -- the operations the API calls -------------------------------------------

@dataclass
class ServerState:
    server: str
    agent_key: str
    status: str  # in_sync | missing | differs | needs_confirm | unsupported | disabled
    detail: str = ""
    fields: list[str] = field(default_factory=list)  # differing field NAMES only

    def to_dict(self) -> dict:
        return {"server": self.server, "agent_key": self.agent_key, "status": self.status,
                "detail": self.detail, "fields": self.fields}


@dataclass
class SyncReport:
    path: str
    servers: list[ServerState]
    stale: list[str]  # our entries in the file that the app no longer wants
    commands_ran: list[str] = field(default_factory=list)
    applied: bool = False

    @property
    def in_sync(self) -> bool:
        return all(s.status in ("in_sync", "unsupported", "disabled", "needs_confirm") for s in self.servers) \
            and not self.stale

    def to_dict(self) -> dict:
        return {"path": self.path, "servers": [s.to_dict() for s in self.servers], "stale": self.stale,
                "commands_ran": self.commands_ran, "applied": self.applied}


def _classify(entries: list[McpEntry], store: McpStore, confirm_secrets: bool):
    have = store.read_managed()
    states: list[ServerState] = []
    desired: dict[str, dict] = {}
    for e in entries:
        if e.transport not in SUPPORTED_TRANSPORTS:
            states.append(ServerState(e.name, e.key, "unsupported", f"transport '{e.transport}' isn't supported here"))
            continue
        want = native_entry(e, store.profile)
        if e.has_secrets and not confirm_secrets:
            # Not written; but if an old copy is there, don't call it in sync.
            states.append(ServerState(e.name, e.key, "needs_confirm",
                                      "has env/header values; confirm to write them in plain text"))
            continue
        desired[e.key] = want
        if e.key not in have:
            states.append(ServerState(e.name, e.key, "missing"))
        else:
            diff = differing_fields(want, have[e.key])
            states.append(ServerState(e.name, e.key, "differs" if diff else "in_sync", fields=diff))
    # Entries held back for secrets keep whatever is already there (not stale).
    keep = {e.key for e in entries}
    stale = sorted(k for k in have if k not in keep)
    return states, desired, stale, have


def status(entries: list[McpEntry], store: McpStore) -> SyncReport:
    states, _desired, stale, _have = _classify(entries, store, confirm_secrets=True)
    # status never writes, so secrets-needing servers are compared like any other
    return SyncReport(str(store.path), states, stale)


def sync(entries: list[McpEntry], store: McpStore, *, confirm_secrets: bool = False) -> SyncReport:
    states, desired, stale, have = _classify(entries, store, confirm_secrets)
    # keep held-back servers' existing entries untouched
    for e in entries:
        if e.key not in desired and e.key in have:
            desired[e.key] = have[e.key]
    ran = store.write_managed(desired)
    after = _classify(entries, store, confirm_secrets)
    final_states = after[0]
    # servers we deliberately held back keep their needs_confirm label
    held = {s.server for s in states if s.status in ("needs_confirm", "unsupported")}
    final_states = [s for s in states if s.server in held] + [s for s in final_states if s.server not in held]
    return SyncReport(str(store.path), final_states, after[2], ran, applied=True)


def redact(text: str, secrets: list[str]) -> str:
    for s in sorted({x for x in secrets if x and len(x) >= 4}, key=len, reverse=True):
        text = text.replace(s, "***")
    return text


def entry_secrets(entry: McpEntry) -> list[str]:
    return list(entry.env.values()) + list(entry.headers.values())
