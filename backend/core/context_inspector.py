"""
backend/core/context_inspector.py

Context Inspector (roadmap: "only meaningful once there's a real agent
loop -- something to take apart"). Answers: if this chat sent its next
request right now, what would fill the model's context window? Broken
into the four things that compete for it:

  system            -- system-role messages
  tool_definitions  -- the `tools` array offered to the model
  conversation      -- user + assistant messages
  tool_results      -- role="tool" messages (usually the surprise: one
                       big file read can outweigh the whole conversation)

It measures what core/agent_loop.py would actually send
(`offered_tool_specs` / `history_for_completion` are the loop's own
builders), so the numbers can't describe a request nothing would make.

Token counts: when the runtime is online, llama-server's own `/tokenize`
is used (one call per category) -- the model's real tokenizer. Otherwise,
or if that call fails, a character-based estimate; the result says which
(`method`), because an estimate for Persian/CJK text in particular can be
well off. Either way the figure excludes the chat template's per-message
framing tokens, so `last_prompt_tokens` (what the server actually
reported for the previous request) is returned alongside as ground truth.
"""

from __future__ import annotations

import json
import math
from typing import Optional
from urllib import request as urlrequest

from sqlalchemy.orm import Session

from . import agent_loop
from ..storage import db as storage_db
from ..storage.db import Chat, ChatMessage, MLModel, Runtime

TOKENIZE_TIMEOUT_SECONDS = 3.0
HIGH_THRESHOLD = 0.75
CRITICAL_THRESHOLD = 0.90

CATEGORY_LABELS = {
    "system": "System prompt",
    "tool_definitions": "Tool definitions",
    "conversation": "Conversation",
    "tool_results": "Tool results",
}


def estimate_tokens(text: str) -> int:
    """~4 ASCII chars per token; non-ASCII scripts (Persian, CJK, ...) pack
    far fewer characters into a token, so they count ~1.5 chars each."""
    if not text:
        return 0
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    other = len(text) - ascii_chars
    return math.ceil(ascii_chars / 4 + other / 1.5)


def tokenize_count(endpoint: str, text: str) -> int:
    """Real token count via llama-server's /tokenize. Raises on any failure
    so the caller can fall back to estimating for the WHOLE inspection
    (not per-category -- mixing methods would make the breakdown incoherent)."""
    if not text:
        return 0
    req = urlrequest.Request(
        f"{endpoint}/tokenize",
        data=json.dumps({"content": text}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=TOKENIZE_TIMEOUT_SECONDS) as resp:
        tokens = json.loads(resp.read())["tokens"]
    if not isinstance(tokens, list):
        raise ValueError("unexpected /tokenize response")
    return len(tokens)


def resolve_context_window(runtime: Optional[Runtime], model: Optional[MLModel]) -> tuple[Optional[int], Optional[str]]:
    """The window the runtime was actually started with wins over the
    model's trained maximum -- the model may support 32k but be running
    at 4k, and 4k is what fills up."""
    cfg_ctx = (runtime.config_json or {}).get("ctx_size") if runtime is not None else None
    if isinstance(cfg_ctx, int) and cfg_ctx > 0:
        return cfg_ctx, "runtime_config"
    if model is not None and model.context_length:
        return model.context_length, "model_metadata"
    return None, None


def pressure_status(total_tokens: int, window: Optional[int]) -> str:
    if not window:
        return "UNKNOWN"
    ratio = total_tokens / window
    if ratio > 1.0:
        return "OVER"
    if ratio >= CRITICAL_THRESHOLD:
        return "CRITICAL"
    if ratio >= HIGH_THRESHOLD:
        return "HIGH"
    return "OK"


def _category_of(message: dict) -> str:
    role = message.get("role")
    if role == "system":
        return "system"
    if role == "tool":
        return "tool_results"
    return "conversation"


def _message_text(message: dict) -> str:
    """Content plus any tool calls the assistant attached -- both are tokens
    the model has to read."""
    text = message.get("content") or ""
    calls = message.get("tool_calls")
    if calls:
        text += "\n" + json.dumps(calls, ensure_ascii=False)
    return text


def _preview(text: str, n: int = 80) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= n else one_line[: n - 1] + "…"


def inspect_chat_context(db: Session, chat: Chat, endpoint: Optional[str] = None, top_n: int = 5) -> dict:
    messages = agent_loop.history_for_completion(db, chat.id)
    tool_specs = agent_loop.offered_tool_specs(db, chat)

    texts: dict[str, list[str]] = {k: [] for k in CATEGORY_LABELS}
    counts: dict[str, int] = {k: 0 for k in CATEGORY_LABELS}
    for m in messages:
        cat = _category_of(m)
        texts[cat].append(_message_text(m))
        counts[cat] += 1
    if tool_specs:
        texts["tool_definitions"].append(json.dumps(tool_specs, ensure_ascii=False))
        counts["tool_definitions"] = len(tool_specs)

    method = "estimate"
    tokens: dict[str, int] = {}
    if endpoint:
        try:
            tokens = {k: tokenize_count(endpoint, "\n".join(v)) for k, v in texts.items()}
            method = "tokenizer"
        except Exception:  # noqa: BLE001 - any failure -> consistent estimate for every category
            tokens = {}
    if method == "estimate":
        tokens = {k: estimate_tokens("\n".join(v)) for k, v in texts.items()}

    runtime = db.get(Runtime, chat.runtime_id)
    model = db.get(MLModel, runtime.model_id) if runtime is not None and runtime.model_id else None
    window, window_source = resolve_context_window(runtime, model)
    total = sum(tokens.values())

    categories = [
        {
            "key": k,
            "label": CATEGORY_LABELS[k],
            "tokens": tokens[k],
            "items": counts[k],
            "percent_of_window": round(tokens[k] / window * 100, 1) if window else None,
        }
        for k in CATEGORY_LABELS
    ]

    # Which individual messages are the heavy ones. Ranked by size (chars
    # are a faithful ordering whichever tokenizer was used); the token
    # figure shown is the estimate, labeled as such by the field name.
    stored = storage_db.get_chat_messages(db, chat.id)
    sized = []
    for stored_msg, wire in zip(stored, messages):
        text = _message_text(wire)
        sized.append(
            {
                "message_id": stored_msg.id,
                "role": stored_msg.role,
                "category": _category_of(wire),
                "chars": len(text),
                "estimated_tokens": estimate_tokens(text),
                "preview": _preview(text),
            }
        )
    largest = sorted(sized, key=lambda x: x["chars"], reverse=True)[:top_n]

    last_prompt_tokens = next(
        (m.prompt_tokens for m in reversed(stored) if m.role == "assistant" and m.prompt_tokens is not None), None
    )

    return {
        "chat_id": chat.id,
        "runtime_id": chat.runtime_id,
        "context_window": window,
        "context_window_source": window_source,
        "method": method,
        "categories": categories,
        "total_tokens": total,
        "percent_used": round(total / window * 100, 1) if window else None,
        "headroom_tokens": (window - total) if window else None,
        "status": pressure_status(total, window),
        "last_prompt_tokens": last_prompt_tokens,
        "tool_count": len(tool_specs),
        "largest_items": largest,
    }
