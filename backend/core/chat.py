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
        raise ChatCompletionError(_http_error_message(exc.code, detail, bool(tools))) from exc
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


_JINJA_HINT = (
    " -- the runtime must be started with the \"Jinja chat template\" option "
    "(Settings -> Runtimes -> RoPE & Chat Template) for tool calling to work."
)


def _http_error_message(code: int, detail: str, had_tools: bool) -> str:
    """llama-server's rejection of `tools` on a runtime launched without
    --jinja is cryptic ("tools param requires --jinja flag"); say what to
    click instead. Only when tools were actually sent -- a plain chat that
    fails for another reason must not be blamed on a flag it doesn't use."""
    msg = f"llama-server returned {code}: {detail[:300]}"
    if had_tools and "jinja" in detail.lower():
        msg += _JINJA_HINT
    return msg


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


class ChatCancelled(Exception):
    """The caller asked a streaming completion to stop (its `cancel` event
    was set -- in practice, the browser disconnected or pressed Stop).
    Carries whatever text had already arrived so the agent loop can keep it
    instead of throwing away the half-finished answer the user was reading."""

    def __init__(self, partial_content: str = "") -> None:
        super().__init__("completion cancelled")
        self.partial_content = partial_content


def stream_chat_completion(
    endpoint: str,
    messages: list[dict],
    temperature: float = 0.8,
    max_tokens: Optional[int] = None,
    timeout: float = 120.0,
    tools: Optional[list[dict]] = None,
    on_delta=None,
    cancel=None,
) -> dict:
    """Same contract and return shape as `send_chat_completion`, but asks
    llama-server for `stream: true` and calls `on_delta(text)` for every
    piece of reply text as it arrives. Tool calls arrive as fragments
    (name once, arguments split across chunks, keyed by `index`) and are
    reassembled here, so callers get the same finished
    [{"id","name","arguments"}] list either way.

    `timeout` is per socket read, not for the whole reply -- a long answer
    that keeps producing tokens never trips it; a server that goes silent
    does. `cancel` (a threading.Event) is checked between chunks; setting it
    closes the connection (which stops llama-server generating) and raises
    ChatCancelled with the partial text."""
    body: dict = {
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if tools:
        body["tools"] = tools

    req = urlrequest.Request(
        f"{endpoint}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )

    start = time.time()
    parts: list[str] = []
    raw_calls: dict[int, dict] = {}
    usage: dict = {}
    finished = False

    try:
        resp = urlrequest.urlopen(req, timeout=timeout)
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace") if exc.fp else str(exc)
        raise ChatCompletionError(_http_error_message(exc.code, detail, bool(tools))) from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise ChatCompletionError(f"could not reach the runtime: {exc}") from exc

    try:
        with resp:
            for raw_line in resp:
                if cancel is not None and cancel.is_set():
                    raise ChatCancelled("".join(parts))
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    finished = True
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue  # a malformed frame shouldn't kill an otherwise good stream
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if text:
                        parts.append(text)
                        if on_delta is not None:
                            on_delta(text)
                    for tc in delta.get("tool_calls") or []:
                        slot = raw_calls.setdefault(tc.get("index", 0), {"id": None, "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
    except ChatCancelled:
        raise
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise ChatCompletionError(f"the stream from the runtime broke: {exc}") from exc

    if not finished:
        raise ChatCompletionError("the stream from the runtime ended before it finished")

    return {
        "content": "".join(parts),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "latency_ms": (time.time() - start) * 1000,
        "tool_calls": _parse_tool_calls([raw_calls[i] for i in sorted(raw_calls)]),
    }
