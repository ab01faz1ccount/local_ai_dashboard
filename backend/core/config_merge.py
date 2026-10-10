"""
backend/core/config_merge.py

How a PATCHed `config_json` is applied to the stored one.

Merge, not replace -- saving `{"ctx_size": 16384}` must not wipe every other
flag the user tuned. But a pure merge can never REMOVE a key, so a field the
user clears in Settings silently kept its old value and went on being passed
to llama-server. A `null` in the patch now means "forget this key".
"""

from __future__ import annotations

from typing import Any, Optional


def merge_config_patch(existing: Optional[dict], patch: dict) -> dict:
    merged: dict[str, Any] = dict(existing or {})
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged
