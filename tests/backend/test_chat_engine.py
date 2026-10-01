"""
tests/backend/test_chat_engine.py

core/chat.py against a real HTTP server (fake_llama_server.py) --
request shape, tool_calls parsing, and every error path (non-200,
malformed response, unreachable host, timeout).
"""

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
