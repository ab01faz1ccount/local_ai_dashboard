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
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Union

ResponseSpec = Union[dict, Callable[[dict], dict]]


class FakeLlamaServer:
    def __init__(self, responses: list[ResponseSpec]):
        self.responses = responses
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

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
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
