"""
tests/backend/fake_llama_server.py

A real HTTP server (stdlib http.server, no mocks) that speaks just
enough of llama-server's OpenAI-compatible `/v1/chat/completions` to
drive core/chat.py and core/agent_loop.py against actual HTTP requests
-- real JSON encoding/decoding, a real socket, real timeouts -- instead
of a monkeypatched function.

Usage:
    server = FakeLlamaServer([{"content": "hi"}, {"tool_calls": [...]}])
    server.start()
    ...  # server.endpoint is "http://127.0.0.1:<port>"
    server.stop()

Each entry in `responses` is returned in order, one per request; the
last entry repeats for any further request past the end of the list.
An entry can be:
  - a dict shaped like `{"content": ..., "tool_calls": [...]}` -> a normal
    200 JSON response in llama-server's shape
  - {"status": <int>, "body": <str>} -> a raw error response
  - {"sleep": <seconds>} -> stalls before answering (for timeout tests);
    combine with "content" to answer after the delay
  - a callable -> called with the parsed request body, must return one
    of the above shapes (for asserting *what* was sent, e.g. that
    `tools` or `tool_call_id` made it onto the wire)

When the request has `"stream": true` the same entry is delivered as a real
text/event-stream: `content` split into one-word deltas, each tool call as
two argument fragments (so the client's reassembly is exercised), then a
usage chunk (only if `stream_options.include_usage`) and `data: [DONE]`.
Extra keys for streaming: `stream_delay` (seconds between chunks),
`stream_break_after` (close the socket after N chunks, no [DONE]).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Union

ResponseSpec = Union[dict, Callable[[dict], dict]]


class FakeLlamaServer:
    def __init__(self, responses: list[ResponseSpec], tokenize: Callable[[str], int] | None = None):
        self.responses = responses
        # /tokenize: returns this many tokens for the given text (default: one
        # per whitespace-separated word). `tokenize_requests` is kept apart
        # from `requests` so chat-completion assertions never see them.
        self.tokenize = tokenize or (lambda text: len(text.split()))
        self.tokenize_requests: list[dict] = []
        self.requests: list[dict] = []
        self._index = 0
        self._lock = threading.Lock()
        self._httpd: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def endpoint(self) -> str:
        assert self._httpd is not None
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def set_responses(self, responses: list[ResponseSpec]) -> None:
        """Replaces the response queue AND resets which one comes next --
        plain `self.responses = ...` alone would leave `_index` wherever
        a previous turn left it."""
        with self._lock:
            self.responses = responses
            self._index = 0

    def start(self) -> "FakeLlamaServer":
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence stdlib's access log
                pass

            def _send_stream(self, body: dict, spec: dict) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

                chunks: list[dict] = []
                content = spec.get("content") or ""
                words = content.split(" ") if content else []
                for i, w in enumerate(words):
                    chunks.append({"choices": [{"delta": {"content": w + (" " if i < len(words) - 1 else "")}}]})
                for idx, call in enumerate(spec.get("tool_calls") or []):
                    fn = call.get("function") or {}
                    args = fn.get("arguments") or ""
                    if not isinstance(args, str):
                        args = json.dumps(args)
                    half = max(len(args) // 2, 1)
                    chunks.append({"choices": [{"delta": {"tool_calls": [
                        {"index": idx, "id": call.get("id"), "type": "function",
                         "function": {"name": fn.get("name"), "arguments": args[:half]}}]}}]})
                    chunks.append({"choices": [{"delta": {"tool_calls": [
                        {"index": idx, "function": {"arguments": args[half:]}}]}}]})
                if (body.get("stream_options") or {}).get("include_usage"):
                    chunks.append({"choices": [], "usage": {
                        "prompt_tokens": spec.get("prompt_tokens", 10), "completion_tokens": spec.get("completion_tokens", 5)}})

                delay = spec.get("stream_delay", 0)
                break_after = spec.get("stream_break_after")
                try:
                    for n, chunk in enumerate(chunks):
                        if break_after is not None and n >= break_after:
                            return  # drop the connection mid-stream, no [DONE]
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                        self.wfile.flush()
                        if delay:
                            time.sleep(delay)
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the client went away (a cancel) -- not an error here

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/tokenize":
                    with outer._lock:
                        outer.tokenize_requests.append(body)
                    n = outer.tokenize(body.get("content", ""))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"tokens": list(range(n))}).encode())
                    return
                with outer._lock:
                    outer.requests.append(body)
                    idx = min(outer._index, len(outer.responses) - 1)
                    outer._index += 1
                spec = outer.responses[idx]
                if callable(spec):
                    spec = spec(body)

                if "sleep" in spec:
                    time.sleep(spec["sleep"])
                if "status" in spec:
                    payload = spec.get("body", "")
                    self.send_response(spec["status"])
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(payload.encode() if isinstance(payload, str) else json.dumps(payload).encode())
                    return

                if body.get("stream"):
                    self._send_stream(body, spec)
                    return

                message: dict[str, Any] = {"content": spec.get("content", "")}
                if spec.get("tool_calls") is not None:
                    message["tool_calls"] = spec["tool_calls"]
                out = {
                    "choices": [{"message": message}],
                    "usage": {
                        "prompt_tokens": spec.get("prompt_tokens", 10),
                        "completion_tokens": spec.get("completion_tokens", 5),
                    },
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(out).encode())

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
