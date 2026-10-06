"""
tests/backend/test_chat_engine.py

core/chat.py against a real HTTP server (fake_llama_server.py) --
request shape, tool_calls parsing, and every error path (non-200,
malformed response, unreachable host, timeout).
"""

import json

import pytest

from backend.core import chat as chat_engine
from fake_llama_server import FakeLlamaServer


@pytest.fixture
def server():
    s = FakeLlamaServer([{"content": "hi there"}])
    s.start()
    yield s
    s.stop()


def test_basic_completion(server):
    result = chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "hello"}])
    assert result["content"] == "hi there"
    assert result["prompt_tokens"] == 10 and result["completion_tokens"] == 5
    assert result["tool_calls"] == []
    assert result["latency_ms"] > 0


def test_request_shape_sent_to_the_server(server):
    chat_engine.send_chat_completion(
        server.endpoint, [{"role": "user", "content": "hi"}], temperature=0.3, max_tokens=100
    )
    req = server.requests[0]
    assert req["messages"] == [{"role": "user", "content": "hi"}]
    assert req["temperature"] == 0.3 and req["max_tokens"] == 100 and req["stream"] is False
    assert "tools" not in req  # omitted entirely when not given, not sent as null/[]


def test_tools_param_is_passed_through_verbatim(server):
    tools = [{"type": "function", "function": {"name": "echo", "parameters": {}}}]
    chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "hi"}], tools=tools)
    assert server.requests[0]["tools"] == tools


def test_empty_tools_list_is_also_omitted(server):
    chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "hi"}], tools=[])
    assert "tools" not in server.requests[0]


def test_tool_calls_are_parsed_with_json_decoded_arguments(server):
    server.responses = [{
        "content": "",
        "tool_calls": [
            {"id": "call_1", "function": {"name": "echo", "arguments": '{"text": "hi"}'}},
            {"id": "call_2", "function": {"name": "add", "arguments": '{"a": 1, "b": 2}'}},
        ],
    }]
    result = chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "hi"}])
    assert result["tool_calls"] == [
        {"id": "call_1", "name": "echo", "arguments": {"text": "hi"}},
        {"id": "call_2", "name": "add", "arguments": {"a": 1, "b": 2}},
    ]


def test_tool_call_missing_id_gets_a_fallback(server):
    server.responses = [{"content": "", "tool_calls": [{"function": {"name": "echo", "arguments": "{}"}}]}]
    result = chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "hi"}])
    assert result["tool_calls"][0]["id"] == "call_0"


def test_malformed_tool_call_arguments_become_empty_dict_not_a_crash(server):
    server.responses = [{"content": "", "tool_calls": [{"id": "c1", "function": {"name": "echo", "arguments": "{not json"}}]}]
    result = chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "hi"}])
    assert result["tool_calls"] == [{"id": "c1", "name": "echo", "arguments": {}}]


def test_tool_call_arguments_already_a_dict_is_accepted(server):
    server.responses = [{"content": "", "tool_calls": [{"id": "c1", "function": {"name": "echo", "arguments": {"text": "x"}}}]}]
    result = chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "hi"}])
    assert result["tool_calls"][0]["arguments"] == {"text": "x"}


def test_null_content_with_tool_calls_becomes_empty_string(server):
    server.responses = [{"content": None, "tool_calls": [{"id": "c1", "function": {"name": "x", "arguments": "{}"}}]}]
    result = chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "hi"}])
    assert result["content"] == ""


def test_http_error_status_raises_chat_completion_error(server):
    server.responses = [{"status": 500, "body": "internal error"}]
    with pytest.raises(chat_engine.ChatCompletionError, match="500"):
        chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "hi"}])


def test_malformed_response_shape_raises(server):
    server.responses = [{"status": 200, "body": '{"nope": true}'}]
    with pytest.raises(chat_engine.ChatCompletionError):
        chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "hi"}])


def test_unreachable_host_raises():
    with pytest.raises(chat_engine.ChatCompletionError, match="could not reach"):
        chat_engine.send_chat_completion("http://127.0.0.1:1", [{"role": "user", "content": "hi"}], timeout=2)


def test_timeout_raises(server):
    server.responses = [{"sleep": 2, "content": "late"}]
    with pytest.raises(chat_engine.ChatCompletionError):
        chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "hi"}], timeout=0.3)


def test_multiple_requests_get_their_own_response_in_order(server):
    server.responses = [{"content": "first"}, {"content": "second"}]
    r1 = chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "a"}])
    r2 = chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "b"}])
    assert (r1["content"], r2["content"]) == ("first", "second")


def test_responses_list_exhausted_repeats_the_last_one(server):
    server.responses = [{"content": "only"}]
    r1 = chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "a"}])
    r2 = chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "b"}])
    assert r1["content"] == r2["content"] == "only"


# ---------------------------------------------------------------------
# --jinja hint
# ---------------------------------------------------------------------

TOOLS = [{"type": "function", "function": {"name": "echo", "parameters": {}}}]


def test_a_jinja_rejection_of_tools_says_what_to_click(server):
    server.responses = [{"status": 500, "body": "tools param requires --jinja flag"}]
    with pytest.raises(chat_engine.ChatCompletionError) as exc:
        chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "hi"}], tools=TOOLS)
    assert "Jinja chat template" in str(exc.value) and "500" in str(exc.value)


def test_the_hint_is_not_added_when_no_tools_were_sent(server):
    server.responses = [{"status": 500, "body": "something about jinja"}]
    with pytest.raises(chat_engine.ChatCompletionError) as exc:
        chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "hi"}])
    assert "Jinja chat template" not in str(exc.value)


def test_the_hint_is_not_added_for_unrelated_errors_with_tools(server):
    server.responses = [{"status": 500, "body": "out of memory"}]
    with pytest.raises(chat_engine.ChatCompletionError) as exc:
        chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "hi"}], tools=TOOLS)
    assert "Jinja chat template" not in str(exc.value)


# ---------------------------------------------------------------------
# stream_chat_completion (real SSE over HTTP)
# ---------------------------------------------------------------------

import threading
import time


def stream(server, **kw):
    return chat_engine.stream_chat_completion(server.endpoint, [{"role": "user", "content": "hi"}], **kw)


def test_stream_returns_the_same_shape_as_the_blocking_call(server):
    server.responses = [{"content": "hello there friend", "prompt_tokens": 12, "completion_tokens": 3}]
    r = stream(server)
    assert r["content"] == "hello there friend"
    assert r["prompt_tokens"] == 12 and r["completion_tokens"] == 3
    assert r["tool_calls"] == [] and r["latency_ms"] > 0
    assert set(r) == set(chat_engine.send_chat_completion(server.endpoint, [{"role": "user", "content": "x"}]))


def test_stream_delivers_deltas_incrementally_in_order(server):
    server.responses = [{"content": "one two three four"}]
    seen = []
    r = stream(server, on_delta=seen.append)
    assert seen == ["one ", "two ", "three ", "four"]
    assert "".join(seen) == r["content"]


def test_stream_request_asks_for_streaming_and_usage(server):
    server.responses = [{"content": "x"}]
    stream(server, temperature=0.2, max_tokens=50)
    req = server.requests[0]
    assert req["stream"] is True and req["stream_options"] == {"include_usage": True}
    assert req["temperature"] == 0.2 and req["max_tokens"] == 50 and "tools" not in req


def test_stream_passes_tools_through(server):
    server.responses = [{"content": "x"}]
    stream(server, tools=TOOLS)
    assert server.requests[0]["tools"] == TOOLS


def test_stream_reassembles_fragmented_tool_calls(server):
    server.responses = [{
        "content": "",
        "tool_calls": [
            {"id": "call_a", "function": {"name": "echo", "arguments": '{"text": "hello world"}'}},
            {"id": "call_b", "function": {"name": "add", "arguments": '{"a": 1, "b": 2}'}},
        ],
    }]
    r = stream(server)
    assert r["tool_calls"] == [
        {"id": "call_a", "name": "echo", "arguments": {"text": "hello world"}},
        {"id": "call_b", "name": "add", "arguments": {"a": 1, "b": 2}},
    ]


def test_stream_text_and_tool_calls_together(server):
    server.responses = [{"content": "let me check", "tool_calls": [{"id": "c", "function": {"name": "echo", "arguments": "{}"}}]}]
    seen = []
    r = stream(server, on_delta=seen.append)
    assert r["content"] == "let me check" and r["tool_calls"][0]["name"] == "echo"
    assert "".join(seen) == "let me check"


def test_stream_empty_reply(server):
    server.responses = [{"content": ""}]
    r = stream(server)
    assert r["content"] == "" and r["tool_calls"] == []


def test_stream_http_error_status(server):
    server.responses = [{"status": 500, "body": "boom"}]
    with pytest.raises(chat_engine.ChatCompletionError, match="500"):
        stream(server)


def test_stream_jinja_hint_applies_here_too(server):
    server.responses = [{"status": 500, "body": "tools param requires --jinja flag"}]
    with pytest.raises(chat_engine.ChatCompletionError) as exc:
        stream(server, tools=TOOLS)
    assert "Jinja chat template" in str(exc.value)


def test_stream_unreachable_host():
    with pytest.raises(chat_engine.ChatCompletionError, match="could not reach"):
        chat_engine.stream_chat_completion("http://127.0.0.1:1", [{"role": "user", "content": "hi"}], timeout=2)


def test_stream_that_drops_midway_is_an_error_not_a_truncated_success(server):
    server.responses = [{"content": "one two three four five", "stream_break_after": 2}]
    seen = []
    with pytest.raises(chat_engine.ChatCompletionError, match="ended before it finished"):
        stream(server, on_delta=seen.append)
    assert seen == ["one ", "two "]  # what did arrive was still delivered live


def test_stream_silent_server_times_out_per_read(server):
    server.responses = [{"sleep": 2, "content": "late"}]
    t0 = time.time()
    with pytest.raises(chat_engine.ChatCompletionError):
        stream(server, timeout=0.3)
    assert time.time() - t0 < 2


def test_stream_slow_but_steady_reply_does_not_trip_the_per_read_timeout(server):
    server.responses = [{"content": "a b c d e", "stream_delay": 0.15}]
    r = stream(server, timeout=0.5)  # whole reply takes >0.7s, each gap is 0.15s
    assert r["content"] == "a b c d e"


def test_stream_cancel_raises_with_the_partial_text_and_stops_reading(server):
    server.responses = [{"content": "w1 w2 w3 w4 w5 w6 w7 w8", "stream_delay": 0.1}]
    cancel = threading.Event()
    seen = []

    def on_delta(t):
        seen.append(t)
        if len(seen) == 3:
            cancel.set()

    with pytest.raises(chat_engine.ChatCancelled) as exc:
        stream(server, on_delta=on_delta, cancel=cancel)
    assert exc.value.partial_content.startswith("w1 w2 w3")
    assert len(exc.value.partial_content.split()) < 8


def test_stream_cancel_already_set_stops_immediately(server):
    server.responses = [{"content": "a b c"}]
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(chat_engine.ChatCancelled) as exc:
        stream(server, cancel=cancel)
    assert exc.value.partial_content == ""


import contextlib
import http.server
import socketserver


@contextlib.contextmanager
def raw_sse_server(frames):
    """A server that sends exactly these raw SSE frames -- for shapes the
    fake llama server never produces (comments, garbage, no usage chunk)."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for frame in frames:
                self.wfile.write(frame.encode())
                self.wfile.flush()

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_stream_ignores_comment_and_malformed_frames():
    frames = [": keepalive\n\n", "data: {not json}\n\n", 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n', "data: [DONE]\n\n"]
    with raw_sse_server(frames) as endpoint:
        assert chat_engine.stream_chat_completion(endpoint, [{"role": "user", "content": "hi"}])["content"] == "ok"


def test_stream_without_a_usage_chunk_gives_none_tokens_not_a_crash():
    frames = ['data: {"choices":[{"delta":{"content":"hi"}}]}\n\n', "data: [DONE]\n\n"]
    with raw_sse_server(frames) as endpoint:
        r = chat_engine.stream_chat_completion(endpoint, [{"role": "user", "content": "hi"}])
    assert r["content"] == "hi" and r["prompt_tokens"] is None and r["completion_tokens"] is None


def test_stream_tool_call_name_and_args_fragments_across_many_chunks():
    d = lambda tc: "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [tc]}}]}) + "\n\n"
    frames = [
        d({"index": 0, "id": "c1", "function": {"name": "ec", "arguments": '{"te'}}),
        d({"index": 0, "function": {"name": "ho", "arguments": 'xt": "a'}}),
        d({"index": 0, "function": {"arguments": 'b"}'}}),
        "data: [DONE]\n\n",
    ]
    with raw_sse_server(frames) as endpoint:
        r = chat_engine.stream_chat_completion(endpoint, [{"role": "user", "content": "hi"}])
    assert r["tool_calls"] == [{"id": "c1", "name": "echo", "arguments": {"text": "ab"}}]


def test_stream_tool_call_with_broken_json_arguments_becomes_empty_dict():
    d = "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c", "function": {"name": "x", "arguments": "{oops"}}]}}]}) + "\n\n"
    with raw_sse_server([d, "data: [DONE]\n\n"]) as endpoint:
        r = chat_engine.stream_chat_completion(endpoint, [{"role": "user", "content": "hi"}])
    assert r["tool_calls"] == [{"id": "c", "name": "x", "arguments": {}}]
