"""Handing the app's MCP servers to an external agent's own config file."""

import json

import pytest

from backend.core.agents import generic_cli, mcp_sync
from backend.core.agents.mcp_sync import McpEntry, McpStore, McpSyncError

FS = McpEntry("Files", "stdio", command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "E:/work"])
WEB = McpEntry("web api", "http", url="http://127.0.0.1:9000/mcp")


def hermes(tmp_path):
    return McpStore(tmp_path / "config.yaml", mcp_sync.HERMES_PROFILE)


def oi(tmp_path):
    return McpStore(tmp_path / "config.toml", mcp_sync.OPEN_INTERPRETER_PROFILE)


def test_agent_key_is_a_safe_prefixed_slug():
    assert mcp_sync.agent_key("Files") == "lai_files"
    assert mcp_sync.agent_key("My Server! v2") == "lai_my_server_v2"
    assert mcp_sync.agent_key("***") == "lai_server"


def test_yaml_writes_stdio_and_http_entries_and_reads_them_back(tmp_path):
    store = hermes(tmp_path)
    report = mcp_sync.sync([FS, WEB], store)
    assert [s.status for s in report.servers] == ["in_sync", "in_sync"]
    got = store.read_managed()
    assert got["lai_files"] == {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "E:/work"]}
    assert got["lai_web_api"] == {"url": "http://127.0.0.1:9000/mcp"}
    assert report.in_sync and report.applied


@pytest.mark.parametrize("make", [hermes, oi])
def test_a_second_sync_changes_nothing(tmp_path, make):
    store = make(tmp_path)
    mcp_sync.sync([FS], store)
    before = store.path.read_text(encoding="utf-8")
    report = mcp_sync.sync([FS], store)
    assert report.commands_ran == []
    assert store.path.read_text(encoding="utf-8") == before


def test_yaml_keeps_everything_else_comments_and_the_users_own_servers(tmp_path):
    store = hermes(tmp_path)
    store.path.write_text(
        "# my hermes\nmodel:\n  default: qwen  # local\n  base_url: http://127.0.0.1:8080/v1\n"
        "mcp_servers:\n  mine:\n    command: uvx\n    args: [tool]\n"
        "# about display\ndisplay:\n  skin: dark\n",
        encoding="utf-8",
    )
    mcp_sync.sync([FS], store)
    text = store.path.read_text(encoding="utf-8")
    assert "# my hermes" in text and "default: qwen  # local" in text
    assert "# about display" in text and "skin: dark" in text
    allv = store.read_all()
    assert allv["mine"] == {"command": "uvx", "args": ["tool"]}
    assert "lai_files" in allv


def test_the_original_file_is_backed_up_once_only(tmp_path):
    store = hermes(tmp_path)
    store.path.write_text("model:\n  default: a\n", encoding="utf-8")
    mcp_sync.sync([FS], store)
    backup = tmp_path / "config.yaml.lai-backup"
    assert backup.read_text(encoding="utf-8") == "model:\n  default: a\n"
    mcp_sync.sync([WEB], store)
    assert backup.read_text(encoding="utf-8") == "model:\n  default: a\n"


def test_changed_server_shows_as_differs_then_syncs(tmp_path):
    store = hermes(tmp_path)
    mcp_sync.sync([FS], store)
    changed = McpEntry("Files", "stdio", command="npx", args=["-y", "x", "E:/other"])
    st = mcp_sync.status([changed], store)
    assert st.servers[0].status == "differs" and st.servers[0].fields == ["args"]
    mcp_sync.sync([changed], store)
    assert mcp_sync.status([changed], store).servers[0].status == "in_sync"


def test_status_never_writes(tmp_path):
    store = hermes(tmp_path)
    st = mcp_sync.status([FS], store)
    assert st.servers[0].status == "missing" and not store.path.exists()


def test_detaching_removes_only_our_entry(tmp_path):
    store = hermes(tmp_path)
    store.path.write_text("mcp_servers:\n  mine:\n    command: uvx\n", encoding="utf-8")
    mcp_sync.sync([FS, WEB], store)
    st = mcp_sync.status([FS], store)
    assert st.stale == ["lai_web_api"]
    report = mcp_sync.sync([FS], store)
    assert report.stale == []
    keys = set(store.read_all())
    assert keys == {"mine", "lai_files"}


def test_removing_the_last_entry_leaves_a_valid_file(tmp_path):
    store = hermes(tmp_path)
    store.path.write_text("model:\n  default: a\n", encoding="utf-8")
    mcp_sync.sync([FS], store)
    mcp_sync.sync([], store)
    assert store.read_all() == {}
    assert "default: a" in store.path.read_text(encoding="utf-8")


def test_secrets_are_held_back_until_confirmed(tmp_path):
    store = hermes(tmp_path)
    e = McpEntry("Brave", "stdio", command="npx", args=["x"], env={"BRAVE_API_KEY": "sk-very-secret-123"})
    report = mcp_sync.sync([e], store)
    assert report.servers[0].status == "needs_confirm"
    assert not store.path.exists() or "sk-very-secret-123" not in store.path.read_text(encoding="utf-8")
    report = mcp_sync.sync([e], store, confirm_secrets=True)
    assert report.servers[0].status == "in_sync"
    assert store.read_managed()["lai_brave"]["env"] == {"BRAVE_API_KEY": "sk-very-secret-123"}


def test_reports_never_carry_secret_values(tmp_path):
    store = oi(tmp_path)
    e = McpEntry("Brave", "http", url="http://x.test/mcp", headers={"Authorization": "Bearer top-secret-token"})
    mcp_sync.sync([e], store, confirm_secrets=True)
    changed = McpEntry("Brave", "http", url="http://x.test/mcp", headers={"Authorization": "Bearer other-secret"})
    st = mcp_sync.status([changed], store)
    dumped = json.dumps(st.to_dict())
    assert "top-secret-token" not in dumped and "other-secret" not in dumped
    assert st.servers[0].fields == ["http_headers.Authorization"]


def test_unsupported_transport_is_reported_not_written(tmp_path):
    store = hermes(tmp_path)
    report = mcp_sync.sync([McpEntry("Old", "sse", url="http://h/sse")], store)
    assert report.servers[0].status == "unsupported"
    assert store.read_managed() == {}


def test_toml_profile_uses_open_interpreters_field_names(tmp_path):
    store = oi(tmp_path)
    store.path.write_text('model = "x"\n\n[model_providers.local]\nbase_url = "http://h/v1"\n', encoding="utf-8")
    e = McpEntry("Web", "http", url="http://127.0.0.1:9/mcp", headers={"X-A": "1"}, disabled_tools=["zap", "alpha"])
    mcp_sync.sync([FS, e], store, confirm_secrets=True)
    import tomllib

    data = tomllib.loads(store.path.read_text(encoding="utf-8"))
    assert data["model"] == "x" and data["model_providers"]["local"]["base_url"] == "http://h/v1"
    assert data["mcp_servers"]["lai_web"] == {
        "url": "http://127.0.0.1:9/mcp", "http_headers": {"X-A": "1"}, "disabled_tools": ["alpha", "zap"]}
    assert data["mcp_servers"]["lai_files"]["command"] == "npx"


def test_hermes_profile_excludes_disabled_tools_under_tools(tmp_path):
    store = hermes(tmp_path)
    e = McpEntry("Files", "stdio", command="npx", args=["a"], disabled_tools=["write_file"])
    mcp_sync.sync([e], store)
    assert store.read_managed()["lai_files"]["tools"] == {"exclude": ["write_file"]}


def test_a_broken_file_is_left_untouched(tmp_path):
    store = hermes(tmp_path)
    store.path.write_text("model: [unclosed\n", encoding="utf-8")
    with pytest.raises(McpSyncError):
        mcp_sync.sync([FS], store)
    assert store.path.read_text(encoding="utf-8") == "model: [unclosed\n"
    assert not (tmp_path / "config.yaml.lai-backup").exists()


def test_toml_with_unwritable_structure_is_left_untouched(tmp_path):
    store = oi(tmp_path)
    text = '[[hooks]]\nname = "a"\n'
    store.path.write_text(text, encoding="utf-8")
    with pytest.raises(McpSyncError):
        mcp_sync.sync([FS], store)
    assert store.path.read_text(encoding="utf-8") == text


def test_mcp_block_that_is_not_a_table_is_refused(tmp_path):
    store = hermes(tmp_path)
    store.path.write_text("mcp_servers: nope\n", encoding="utf-8")
    with pytest.raises(McpSyncError):
        mcp_sync.sync([FS], store)


def test_redact_hides_values_but_ignores_trivial_ones():
    assert mcp_sync.redact("key=abcd1234 ok", ["abcd1234", "ok"]) == "key=*** ok"


def test_generic_spec_can_declare_an_mcp_file(tmp_path):
    spec = {
        "name": "mycli", "executable": "mycli",
        "config": {"mode": "env_only"}, "env": {"A": "{base_url}"},
        "mcp": {"format": "toml", "path": str(tmp_path / "m.toml")},
    }
    adapter = generic_cli.GenericCliAdapter(spec)
    store = adapter.mcp_store()
    mcp_sync.sync([FS], store)
    assert store.read_managed()["lai_files"]["command"] == "npx"
    assert generic_cli.GenericCliAdapter({**spec, "mcp": None}).mcp_store() is None


@pytest.mark.parametrize("bad", [{"format": "json", "path": "x"}, {"format": "toml"}, {"format": "toml", "path": "x", "key": "a b"}, "x"])
def test_generic_spec_rejects_a_bad_mcp_section(bad):
    spec = {"name": "c", "executable": "c", "config": {"mode": "env_only"}, "env": {"A": "{base_url}"}, "mcp": bad}
    with pytest.raises(generic_cli.SpecError):
        generic_cli.validate_spec(spec)


def test_real_adapters_expose_their_documented_files(tmp_path, monkeypatch):
    from backend.core import agents

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    h = agents.get_adapter("hermes")
    o = agents.get_adapter("open_interpreter")
    assert h.mcp_store().path == tmp_path / ".hermes" / "config.yaml"
    assert o.mcp_store().path == tmp_path / ".openinterpreter" / "config.toml"
    assert h.mcp_probe_is_live() and not o.mcp_probe_is_live()
