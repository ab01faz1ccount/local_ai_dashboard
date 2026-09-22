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
from .base import AgentAdapter, AgentBackendInfo, get_adapter, list_adapters, register_adapter

__all__ = [
    "AgentAdapter",
    "AgentBackendInfo",
    "get_adapter",
    "list_adapters",
    "register_adapter",
]
