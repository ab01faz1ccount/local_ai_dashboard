"""
backend/core/agents/base.py

AgentAdapter: the same "one concrete implementation per backend" pattern
core/engine/base.py already uses for InferenceEngine/PlatformProvider,
applied to external agent CLIs (Hermes today, others later) instead of
llama.cpp itself.

An adapter's job is exactly the two manual-pain-point items named in the
settings guide's own "challenges" section:
  1. `configure()` -- actually write the agent's own config so it points
     at our runtime instead of a cloud model (what the user was doing by
     hand with `hermes config set ...`).
  2. `build_launch_command()` -- know how to start this agent's real
     interactive CLI process, so the terminal panel (core/agents/
     pty_session.py + api/ws.py) can attach to the genuine thing instead
     of a reimplementation of it.

Nothing about llama.cpp itself lives here -- `--jinja` and friends are
runtime launch flags, already covered by core/engine/llama_cpp_engine.py
and the Settings panel.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentBackendInfo:
    backend_id: str
    display_name: str
    brand_color: str  # hex; themes the terminal panel's chrome in the UI
    detected: bool
    detected_path: Optional[str] = None
    detected_version: Optional[str] = None


class AgentAdapter(ABC):
    backend_id: str
    display_name: str
    brand_color: str

    @abstractmethod
    def detect(self) -> AgentBackendInfo:
        """Checks whether this agent's CLI is installed on this machine."""

    @abstractmethod
    def configure(
        self,
        runtime_base_url: str,
        api_key: str,
        model_name: Optional[str] = None,
        context_length: Optional[int] = None,
    ) -> list[str]:
        """Runs whatever this agent's own CLI needs to point itself at
        our runtime instead of a cloud model. Returns the commands it
        actually ran (as strings), for a confirmation log in the UI.
        Raises RuntimeError with a clear message on failure -- never
        fails silently, since a silently-unconfigured agent is exactly
        the "GUI is just a nominal label" gap this exists to close."""

    @abstractmethod
    def build_launch_command(self, resume_session_id: Optional[str] = None) -> list[str]:
        """The argv to spawn this agent's real interactive CLI under a
        pty -- e.g. ["hermes"] or, to resume a prior session,
        ["hermes", "-r", session_id]."""

    # -- optional: lets the app PROVE configure() took effect -----------------
    # Writing config is only half the job: an agent can accept a `config set`
    # and still ignore it (wrong key name -- Hermes reads `model.default`, not
    # `model.name` -- or a stale default winning). An adapter that implements
    # these two lets core/agents/verification.py read every value back and
    # report exactly which key didn't stick. Both default to "unsupported",
    # which verification reports as `unreadable` rather than as a failure.

    # Settings whose value must never be compared/displayed (API keys...).
    sensitive_settings: frozenset = frozenset()

    def expected_settings(
        self,
        runtime_base_url: str,
        api_key: str,
        model_name: Optional[str] = None,
        context_length: Optional[int] = None,
    ) -> dict[str, str]:
        """The agent-side settings (key -> value) that make this agent talk
        to our runtime. This is the single source of truth: configure()
        writes exactly these and verification reads exactly these back.
        `context_length` is the context window the runtime really serves per
        request (None = unknown); an agent that has such a setting should be
        told, or it plans against its own default, not the server's."""
        return {}

    def launch_env(self, api_key: str = "sk-no-key", **endpoint) -> dict[str, str]:
        """Extra environment variables the agent's process needs (e.g. a key an
        agent reads from its environment instead of storing in its config).
        `endpoint` carries base_url / model_name / context_length for adapters
        (the data-driven generic one) whose variables are built from them."""
        return {}

    def read_setting(self, key: str) -> Optional[str]:
        """The agent's own current value for `key`, as text. Raise
        RuntimeError if it can't be read (command failed / unsupported)."""
        raise RuntimeError(f"{self.display_name} adapter can't read settings back")


_ADAPTERS: dict[str, AgentAdapter] = {}


def register_adapter(adapter: AgentAdapter) -> None:
    _ADAPTERS[adapter.backend_id] = adapter


def get_adapter(backend_id: str) -> Optional[AgentAdapter]:
    return _ADAPTERS.get(backend_id)


def list_adapters() -> list[AgentAdapter]:
    return list(_ADAPTERS.values())
