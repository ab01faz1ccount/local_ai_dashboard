"""
backend/core/chat.py

Sends a message to a running llama-server instance via its
OpenAI-compatible `/v1/chat/completions` endpoint and returns the
assistant's reply plus basic usage stats. Kept separate from
LlamaCppEngine because this is a single request/response concern, not
process lifecycle -- callers only need a runtime's `endpoint` (from
EngineStatus), not the engine object itself.
"""

from __future__ import annotations

import json
import time
from typing import Optional
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError


class ChatCompletionError(Exception):
    """Raised for anything that stops a chat message from getting a
    reply: the runtime unreachable, a timeout, or a response shape we
    don't recognize. The chat API route turns this into a clean error
    for the frontend instead of a raw 500."""


def send_chat_completion(
    endpoint: str,
    messages: list[dict],
    temperature: float = 0.8,
    max_tokens: Optional[int] = None,
    timeout: float = 120.0,
) -> dict:
    """`messages`: [{"role": "user"|"assistant"|"system", "content": "..."}]

    Returns: {"content", "prompt_tokens", "completion_tokens", "latency_ms"}
    """
    body: dict = {"messages": messages, "temperature": temperature, "stream": False}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    req = urlrequest.Request(
        f"{endpoint}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    start = time.time()
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace") if exc.fp else str(exc)
        raise ChatCompletionError(f"llama-server returned {exc.code}: {detail[:300]}") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise ChatCompletionError(f"could not reach the runtime: {exc}") from exc

    latency_ms = (time.time() - start) * 1000

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ChatCompletionError(f"unexpected response shape from llama-server: {exc}") from exc

    usage = payload.get("usage") or {}
    return {
        "content": content,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "latency_ms": latency_ms,
    }
