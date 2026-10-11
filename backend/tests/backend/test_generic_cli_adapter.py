"""The data-driven agent adapter: spec validation, env / cli_set / toml_file modes, secrets."""

import subprocess
from types import SimpleNamespace

import pytest

from backend.core import agents
from backend.core.agents import generic_cli as g
from backend.core.agents.generic_cli import GenericCliAdapter, SpecError, validate_spec

URL = "http://127.0.0.1:8080/v1"

ENV_SPEC = {
    "name": "My agent", "executable": "myagent", "config": {"mode": "env_only"},
    "env": {"OPENAI_BASE_URL": "{base_url}", "OPENAI_API_KEY": "{api_key}", "OPENAI_MODEL": "{model}", "CTX": "{context}"},
}
SET_SPEC = {
    "name": "Setter", "executable": "setter",
    "config": {"mode": "cli_set", "set_command": ["{exe}", "config", "set", "{key}", "{value}"],
               "get_command": ["{exe}", "config", "get", "{key}"]},
    "settings": {"model.base_url": "{base_url}", "model.api_key": "{api_key}", "model.name": "{model}"},
}


def toml_spec(path):
    return {"name": "T", "executable": "t", "config": {"mode": "toml_file", "path": str(path)},
            "settings": {"provider.base_url": "{base_url}", "provider.model": "{model}"}}


# -- validation ---------------------------------------------------------------

@pytest.mark.parametrize("bad, fragment", [
    ("nope", "object"),
    ({"executable": "x", "config": {"mode": "env_only"}, "env": {"A": "{base_url}"}}, "name"),
    ({"name": "n", "config": {"mode": "env_only"}, "env": {"A": "{base_url}"}}, "executable"),
    ({"name": "n", "executable": "rm -rf /", "config": {"mode": "env_only"}, "env": {"A": "{base_url}"}}, "command line"),
    ({"name": "n", "executable": "x", "config": {"mode": "magic"}}, "mode"),
    ({"name": "n", "executable": "x", "config": {"mode": "env_only"}, "env": {"A": "{secret}"}}, "unknown placeholder"),
    ({"name": "n", "executable": "x", "config": {"mode": "env_only"}, "env": {"1BAD": "{base_url}"}}, "variable name"),
    ({"name": "n", "executable": "x", "config": {"mode": "env_only"}}, "env"),
    ({"name": "n", "executable": "x", "config": {"mode": "cli_set", "set_command": ["other", "{key}"]}, "settings": {"a": "{model}"}}, "{exe}"),
    ({"name": "n", "executable": "x", "config": {"mode": "cli_set", "set_command": ["{exe}", "{nope}"]}, "settings": {"a": "{model}"}}, "unknown placeholder"),
    ({"name": "n", "executable": "x", "config": {"mode": "toml_file"}, "settings": {"a": "{model}"}}, "path"),
    ({"name": "n", "executable": "x", "config": {"mode": "env_only"}, "env": {"A": "{model}"}, "launch_args": "x"}, "launch_args"),
])
def test_bad_specs_are_rejected_with_a_reason(bad, fragment):
    with pytest.raises(SpecError) as e:
        validate_spec(bad)
    assert fragment in str(e.value.args[0])


def test_a_valid_spec_is_normalized_with_defaults():
    out = validate_spec(ENV_SPEC)
    assert out["version_args"] == ["--version"] and out["launch_args"] == [] and out["config"] == {"mode": "env_only"}


def test_every_preset_is_a_valid_spec_once_named():
    for key, preset in g.PRESETS.items():
        spec = {"name": key, "executable": "tool", **{k: v for k, v in preset.items() if k != "label"}}
        validate_spec(spec)


# -- env_only -----------------------------------------------------------------

def test_env_only_builds_the_environment_and_skips_unknown_values():
    a = GenericCliAdapter(ENV_SPEC)
    env = a.launch_env("sk-1", base_url=URL, model_name="m", context_length=None)
    assert env == {"OPENAI_BASE_URL": URL, "OPENAI_API_KEY": "sk-1", "OPENAI_MODEL": "m"}  # no CTX: context unknown
    assert a.launch_env("sk-1", base_url=URL, model_name="m", context_length=8192)["CTX"] == "8192"


def test_the_model_falls_back_to_a_placeholder():
    assert GenericCliAdapter(ENV_SPEC).launch_env(base_url=URL)["OPENAI_MODEL"] == "local-model"


def test_env_only_configure_writes_nothing_and_verification_has_nothing_to_check():
    a = GenericCliAdapter(ENV_SPEC)
    assert a.configure(URL, "sk-no-key") == [] and a.expected_settings(URL, "k") == {}


def test_without_an_endpoint_there_is_no_env():
    assert GenericCliAdapter(ENV_SPEC).launch_env("sk-1") == {}


# -- cli_set ------------------------------------------------------------------

def fake_run(monkeypatch, store=None, fail_on=None):
    store = {} if store is None else store
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if fail_on and fail_on in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr="no such key")
        if argv[1:3] == ["config", "set"]:
            store[argv[3]] = argv[4]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[1:3] == ["config", "get"]:
            return SimpleNamespace(returncode=0, stdout=store.get(argv[3], ""), stderr="")
        return SimpleNamespace(returncode=0, stdout="1.2.3\n", stderr="")

    monkeypatch.setattr(g.subprocess, "run", run)
    return store, calls


def test_cli_set_runs_one_command_per_setting_and_masks_the_key(monkeypatch):
    store, calls = fake_run(monkeypatch)
    monkeypatch.setattr(g.shutil, "which", lambda n: "/bin/setter")
    ran = GenericCliAdapter(SET_SPEC).configure(URL, "sk-secret", "m")
    assert store == {"model.base_url": URL, "model.api_key": "sk-secret", "model.name": "m"}
    assert len(calls) == 3 and all(c[0] == "/bin/setter" for c in calls)
    assert not any("sk-secret" in line for line in ran) and any("***" in line for line in ran)


def test_cli_set_failure_names_the_command(monkeypatch):
    fake_run(monkeypatch, fail_on="model.name")
    monkeypatch.setattr(g.shutil, "which", lambda n: "/bin/setter")
    with pytest.raises(RuntimeError) as e:
        GenericCliAdapter(SET_SPEC).configure(URL, "k", "m")
    assert "model.name" in str(e.value) and "no such key" in str(e.value)


def test_cli_set_values_can_be_read_back_and_the_key_is_sensitive(monkeypatch):
    fake_run(monkeypatch, store={"model.base_url": URL})
    monkeypatch.setattr(g.shutil, "which", lambda n: "/bin/setter")
    a = GenericCliAdapter(SET_SPEC)
    assert a.read_setting("model.base_url") == URL
    assert a.sensitive_settings == {"model.api_key"}


def test_a_missing_executable_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(g.shutil, "which", lambda n: None)
    a = GenericCliAdapter(SET_SPEC)
    with pytest.raises(RuntimeError) as e:
        a.configure(URL, "k")
    assert "setter" in str(e.value)
    assert a.detect().detected is False
    with pytest.raises(RuntimeError):
        a.build_launch_command()


def test_set_values_are_passed_as_argv_not_through_a_shell(monkeypatch):
    store, calls = fake_run(monkeypatch)
    monkeypatch.setattr(g.shutil, "which", lambda n: "/bin/setter")
    GenericCliAdapter(SET_SPEC).configure("http://h/v1; rm -rf /", "k", "a b")
    assert ["/bin/setter", "config", "set", "model.base_url", "http://h/v1; rm -rf /"] in calls


# -- toml_file ----------------------------------------------------------------

def test_toml_mode_edits_only_its_keys_and_backs_up_once(tmp_path):
    path = tmp_path / "cfg" / "config.toml"
    path.parent.mkdir()
    path.write_text('theme = "dark"\n[provider]\nretries = 3\n')
    a = GenericCliAdapter(toml_spec(path))
    a.configure(URL, "k", "m")
    a.configure(URL, "k", "m2")
    from backend.core.agents import toml_edit
    data = toml_edit.loads(path.read_text())
    assert data["theme"] == "dark" and data["provider"]["retries"] == 3
    assert data["provider"]["base_url"] == URL and data["provider"]["model"] == "m2"
    backup = path.with_name(path.name + g.BACKUP_SUFFIX)
    assert 'retries = 3' in backup.read_text() and "base_url" not in backup.read_text()


def test_toml_values_are_read_back_for_verification(tmp_path):
    path = tmp_path / "c.toml"
    a = GenericCliAdapter(toml_spec(path))
    assert a.read_setting("provider.model") is None
    a.configure(URL, "k", "m")
    assert a.read_setting("provider.model") == "m"


def test_an_unreadable_toml_is_reported_and_left_alone(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("this is = = not toml")
    with pytest.raises(RuntimeError):
        GenericCliAdapter(toml_spec(path)).configure(URL, "k")
    assert path.read_text() == "this is = = not toml"


# -- wiring -------------------------------------------------------------------

def test_resolve_adapter_builds_one_from_the_agents_own_spec():
    a = agents.resolve_adapter("custom_cli", {"cli_spec": ENV_SPEC})
    assert isinstance(a, GenericCliAdapter) and a.display_name == "My agent"


def test_resolve_adapter_without_a_spec_or_for_generic_is_none():
    assert agents.resolve_adapter("custom_cli", {}) is None
    assert agents.resolve_adapter("generic", {}) is None


def test_resolve_adapter_still_returns_the_registered_ones():
    assert agents.resolve_adapter("hermes", {}) is agents.get_adapter("hermes")


def test_a_stored_spec_that_went_bad_raises_a_spec_error():
    with pytest.raises(SpecError):
        agents.resolve_adapter("custom_cli", {"cli_spec": {"name": "x"}})


def test_known_backends():
    assert agents.is_known_backend("custom_cli") and agents.is_known_backend("hermes")
    assert agents.is_known_backend("generic") and not agents.is_known_backend("nope")


def test_existing_adapters_accept_the_endpoint_keywords():
    for backend in ("hermes", "open_interpreter"):
        agents.get_adapter(backend).launch_env("k", base_url=URL, model_name="m", context_length=1)
