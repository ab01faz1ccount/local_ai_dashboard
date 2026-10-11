"""
backend/core/agents/__init__.py

Importing this package registers every known AgentAdapter (see each
module's `register_adapter(...)` call at import time). `api/http.py` and
`api/ws.py` both do `from ..core import agents` before calling
`agents.list_adapters()` / `agents.get_adapter()`, which is what actually
triggers these imports and registration -- adding a new adapter module
means adding its import here, nothing else.
"""

from __future__ import annotations

from . import hermes_adapter  # noqa: F401  (import triggers registration)
from . import open_interpreter_adapter  # noqa: F401
from .base import AgentAdapter, AgentBackendInfo, get_adapter, list_adapters, register_adapter
from .generic_cli import BACKEND_ID as CUSTOM_BACKEND_ID
from .generic_cli import GenericCliAdapter, SpecError, validate_spec


def is_known_backend(backend_id: str) -> bool:
    return backend_id == "generic" or backend_id == CUSTOM_BACKEND_ID or get_adapter(backend_id) is not None


def resolve_adapter(backend_id: str, config_json: dict | None = None):
    """The adapter that drives an agent: a registered one by id, or -- for the
    data-driven "custom_cli" backend -- one built from the agent's own saved
    spec (config_json["cli_spec"]). None when there is nothing to drive.
    Raises SpecError when a saved spec is no longer valid."""
    if backend_id == CUSTOM_BACKEND_ID:
        spec = (config_json or {}).get("cli_spec")
        return GenericCliAdapter(spec) if spec else None
    return get_adapter(backend_id)

__all__ = [
    "AgentAdapter",
    "AgentBackendInfo",
    "get_adapter",
    "list_adapters",
    "register_adapter",
    "resolve_adapter",
    "is_known_backend",
    "GenericCliAdapter",
    "SpecError",
    "validate_spec",
    "CUSTOM_BACKEND_ID",
]
