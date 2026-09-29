"""
tests/backend/test_mcp_validation.py

The security gate in core/mcp/validation.py. Each "refused" case here is
a way a config could otherwise turn "add an MCP server" into "run
arbitrary code", so they're asserted individually, not as a blob.
"""

import os
import sys

import pytest

from backend.core.mcp import validation as v
from backend.core.mcp.validation import McpValidationError


# ---------------- command ----------------

@pytest.mark.parametrize("cmd", ["npx", "uvx", "uv", "node", "python", "python3", "py", "deno", "bun", "bunx", "docker", "NPX"])
def test_allowlisted_bare_launchers_accepted(cmd):
    assert v.validate_command(cmd) == cmd


def test_absolute_path_to_existing_file_accepted():
    assert v.validate_command(sys.executable) == sys.executable


def test_absolute_path_to_missing_file_refused(tmp_path):
    with pytest.raises(McpValidationError):
        v.validate_command(str(tmp_path / "nope"))


def test_absolute_path_to_a_directory_refused(tmp_path):
    with pytest.raises(McpValidationError):
        v.validate_command(str(tmp_path))


@pytest.mark.parametrize(
    "cmd",
    [
        "sh", "bash", "zsh", "dash", "fish", "cmd", "cmd.exe", "powershell", "pwsh", "PowerShell.exe",
        "/bin/sh", "/bin/bash", "/usr/bin/env", "C:\\Windows\\System32\\cmd.exe",
        "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "sudo", "xargs",
    ],
)
def test_shells_and_wrappers_refused_under_any_spelling(cmd):
    with pytest.raises(McpValidationError):
        v.validate_command(cmd, must_exist=False)


def test_shell_refused_even_when_the_file_really_exists():
    # /bin/sh exists on the machine running the tests -- existence must not be a way past the block.
    if not os.path.isfile("/bin/sh"):
        pytest.skip("no /bin/sh here")
    with pytest.raises(McpValidationError):
        v.validate_command("/bin/sh")


@pytest.mark.parametrize(
    "cmd",
    ["npx; rm -rf ~", "npx && whoami", "npx | tee x", "npx `id`", "npx $(id)", "npx > out", "npx\nid", "np*x", 'npx"', "npx'"],
)
def test_shell_metacharacters_refused(cmd):
    with pytest.raises(McpValidationError):
        v.validate_command(cmd)


@pytest.mark.parametrize("cmd", ["/opt/tool;id", "/opt/tool && id", "/opt/`id`", "/opt/$(id)", "C:\\tools\\a|b.exe", "/opt/tool\nid", "/opt/t*", "/opt/'x'"])
def test_shell_metacharacters_refused_in_absolute_paths_too(cmd):
    # must_exist=False isolates the metacharacter rule from the file-exists check that would otherwise also reject these.
    with pytest.raises(McpValidationError):
        v.validate_command(cmd, must_exist=False)


@pytest.mark.parametrize("cmd", ["npx -y foo", "python server.py", "./run.sh", "bin/tool", "curl", "wget", "ruby", "perl", "not-a-launcher"])
def test_unlisted_or_multi_token_bare_commands_refused(cmd):
    with pytest.raises(McpValidationError):
        v.validate_command(cmd)


@pytest.mark.parametrize("cmd", ["", "   ", None, 5, []])
def test_empty_or_non_string_command_refused(cmd):
    with pytest.raises(McpValidationError):
        v.validate_command(cmd)


# ---------------- args ----------------

def test_args_must_be_a_list_of_strings():
    assert v.validate_args(["-y", "@scope/pkg"]) == ["-y", "@scope/pkg"]
    assert v.validate_args(None) == []
    for bad in ("-y foo", [1, 2], {"a": 1}, ["ok", None]):
        with pytest.raises(McpValidationError):
            v.validate_args(bad)


@pytest.mark.parametrize(
    "cmd,args",
    [
        ("python", ["-c", "import os"]),
        ("python3", ["-c", "print(1)"]),
        ("/usr/bin/python3", ["-c", "print(1)"]),
        ("C:\\Python312\\python.exe", ["-c", "print(1)"]),
        ("node", ["-e", "1"]),
        ("node", ["--eval", "1"]),
        ("node", ["-p", "1"]),
        ("bun", ["-e", "1"]),
        ("deno", ["eval", "1"]),
    ],
)
def test_inline_code_flags_refused(cmd, args):
    with pytest.raises(McpValidationError):
        v.validate_args(args, cmd)


def test_script_file_args_still_fine_for_interpreters():
    assert v.validate_args(["server.py", "--port", "1"], "python") == ["server.py", "--port", "1"]
    assert v.validate_args(["-m", "mypkg"], "python") == ["-m", "mypkg"]


def test_arg_limits():
    with pytest.raises(McpValidationError):
        v.validate_args(["x"] * (v.MAX_ARGS + 1))
    with pytest.raises(McpValidationError):
        v.validate_args(["x" * (v.MAX_ARG_LEN + 1)])
    with pytest.raises(McpValidationError):
        v.validate_args(["a\x00b"])


# ---------------- env ----------------

def test_env_accepts_normal_keys():
    assert v.validate_env({"API_KEY": "abc", "_x1": "y"}) == {"API_KEY": "abc", "_x1": "y"}
    assert v.validate_env(None) == {}


@pytest.mark.parametrize(
    "key",
    ["PATH", "path", "PATHEXT", "COMSPEC", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
     "PYTHONPATH", "PYTHONSTARTUP", "NODE_OPTIONS", "BASH_ENV", "IFS"],
)
def test_program_hijacking_env_vars_refused(key):
    with pytest.raises(McpValidationError):
        v.validate_env({key: "x"})


@pytest.mark.parametrize("key", ["1BAD", "has space", "has-dash", "a=b", "", "é"])
def test_malformed_env_keys_refused(key):
    with pytest.raises(McpValidationError):
        v.validate_env({key: "x"})


def test_env_values_must_be_clean_strings():
    for bad in (5, None, "a\x00b", "x" * (v.MAX_VALUE_LEN + 1)):
        with pytest.raises(McpValidationError):
            v.validate_env({"K": bad})
    with pytest.raises(McpValidationError):
        v.validate_env(["K=V"])


# ---------------- url / headers ----------------

@pytest.mark.parametrize("url", ["http://127.0.0.1:9000/mcp", "https://example.com/sse", "http://localhost/x?a=1"])
def test_good_urls(url):
    assert v.validate_url(url) == url


@pytest.mark.parametrize(
    "url",
    ["ftp://x.com/a", "file:///etc/passwd", "javascript:alert(1)", "ws://x.com", "example.com/mcp", "http://", "http:///path",
     "https://user:pw@example.com/mcp", "https://user@example.com/mcp", "http://exa mple.com", "http://x.com:99999/",
     "", None, 5, "http://x.com/\nHost: evil"],
)
def test_bad_urls_refused(url):
    with pytest.raises(McpValidationError):
        v.validate_url(url)


def test_headers_validation():
    assert v.validate_headers({"Authorization": "Bearer abc", "X-Api-Key": "k"}) == {"Authorization": "Bearer abc", "X-Api-Key": "k"}
    assert v.validate_headers(None) == {}
    for bad in ({"Bad Name": "x"}, {"X": "a\r\nInjected: 1"}, {"X": "a\nb"}, {"": "x"}, {"X": 5}, ["X: y"]):
        with pytest.raises(McpValidationError):
            v.validate_headers(bad)


def test_loopback_detection():
    assert v.is_loopback_url("http://127.0.0.1:1/x")
    assert v.is_loopback_url("http://localhost:1/x")
    assert v.is_loopback_url("http://[::1]:1/x")
    assert not v.is_loopback_url("https://example.com/x")
    assert not v.is_loopback_url("http://localhost.evil.com/x")


# ---------------- name / transport / whole config ----------------

def test_name_rules():
    assert v.validate_name("  my server ") == "my server"
    for bad in ("", "   ", None, "x" * (v.MAX_NAME_LEN + 1), "a\x01b"):
        with pytest.raises(McpValidationError):
            v.validate_name(bad)


def test_transport_rules():
    for t in ("stdio", "http", "sse"):
        assert v.validate_transport(t) == t
    for bad in ("websocket", "STDIO", "", None):
        with pytest.raises(McpValidationError):
            v.validate_transport(bad)


def test_server_config_drops_the_other_transports_fields():
    stdio = v.validate_server_config(transport="stdio", command="npx", args=["-y", "p"], env={"K": "v"}, url="http://x.com", headers={"A": "b"})
    assert stdio["url"] is None and stdio["headers"] == {} and stdio["command"] == "npx"
    http = v.validate_server_config(transport="http", url="http://127.0.0.1:1/mcp", headers={"A": "b"}, command="rm", env={"K": "v"})
    assert http["command"] is None and http["args"] == [] and http["env"] == {}
    assert http["headers"] == {"A": "b"}


def test_server_config_requires_the_transports_own_fields():
    with pytest.raises(McpValidationError):
        v.validate_server_config(transport="stdio")
    with pytest.raises(McpValidationError):
        v.validate_server_config(transport="http")
    with pytest.raises(McpValidationError):
        v.validate_server_config(transport="sse", url="not a url")
