"""
backend/core/chat.py

Sends a message to a running llama-server instance via its
OpenAI-compatible `/v1/chat/completions` endpoint and returns the
assistant's reply plus basic usage stats. Kept separate from
LlamaCppEngine because this is a single request/response concern, not
process lifecycle -- callers only need a runtime's `endpoint` (from
EngineStatus), not the engine object itself.

`tools`, when given, is passed straight through as the OpenAI-style
`tools` array; core/agent_loop.py is what builds that array and
interprets `tool_calls` back out of the response -- this module only
translates between llama-server's wire format and plain Python, the
same job it already does for `content`/`usage`.
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
    tools: Optional[list[dict]] = None,
) -> dict:
    """`messages`: [{"role": "user"|"assistant"|"system"|"tool", "content": "...", ...}]
    `tools`: OpenAI-style function specs, or None to omit the field entirely
    (a server with no tool-calling support gets exactly the request it
    would have gotten before this parameter existed).

    Returns: {"content", "prompt_tokens", "completion_tokens", "latency_ms", "tool_calls"}
    `tool_calls`: [{"id", "name", "arguments"}], "" when the model didn't ask for one.
    """
    body: dict = {"messages": messages, "temperature": temperature, "stream": False}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if tools:
        body["tools"] = tools

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
        message = payload["choices"][0]["message"]
        content = message.get("content") or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise ChatCompletionError(f"unexpected response shape from llama-server: {exc}") from exc

    tool_calls = _parse_tool_calls(message.get("tool_calls"))

    usage = payload.get("usage") or {}
    return {
        "content": content,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "latency_ms": latency_ms,
        "tool_calls": tool_calls,
    }


def _parse_tool_calls(raw: Optional[list]) -> list[dict]:
    """`raw` is the OpenAI-shaped `tool_calls` array (`function.arguments`
    is a JSON-encoded STRING per the spec, not an object). Malformed
    arguments become `{}` rather than raising -- a model that emits
    broken JSON for one call shouldn't take down the whole turn; the
    empty dict simply won't satisfy the tool's schema, so the tool call
    fails cleanly downstream instead of the entire response failing here."""
    if not raw:
        return []
    calls = []
    for i, c in enumerate(raw):
        fn = (c or {}).get("function") or {}
        raw_args = fn.get("arguments")
        if isinstance(raw_args, dict):
            args = raw_args
        else:
            try:
                args = json.loads(raw_args) if raw_args else {}
            except (json.JSONDecodeError, TypeError):
                args = {}
        calls.append({"id": c.get("id") or f"call_{i}", "name": fn.get("name", ""), "arguments": args})
    return calls
