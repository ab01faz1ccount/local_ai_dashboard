"""
backend/core/endpoint.py

The one object that says "this is the OpenAI-compatible server an agent talks
to": base URL, key, model id, context window.

Today every Endpoint comes from a local llama-server runtime. The shape is
deliberately source-agnostic so a hosted provider (an OpenAI-compatible API
with its own key) can be another source later without touching any agent
adapter -- adapters only ever see an Endpoint's fields.

The key is held out of repr()/logging, and `public_dict()` never includes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

NO_KEY = "sk-no-key"  # llama-server ignores the key unless started with --api-key


@dataclass(frozen=True)
class Endpoint:
    base_url: str
    model: Optional[str] = None
    context_length: Optional[int] = None
    api_key: str = field(default=NO_KEY, repr=False)
    source: str = "local_runtime"  # "local_runtime" | "provider" (reserved)
    source_id: Optional[int] = None

    def public_dict(self) -> dict:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "context_length": self.context_length,
            "source": self.source,
            "source_id": self.source_id,
            "has_key": self.api_key != NO_KEY,
        }


def for_runtime(runtime, model_name: Optional[str] = None, context_length: Optional[int] = None, api_key: str = NO_KEY) -> Endpoint:
    """Endpoint of a local runtime row (host/port come from the row)."""
    return Endpoint(
        base_url=f"http://{runtime.host}:{runtime.port}/v1",
        model=model_name,
        context_length=context_length,
        api_key=api_key or NO_KEY,
        source="local_runtime",
        source_id=getattr(runtime, "id", None),
    )
