"""
Open Interpreter adapter: edits ~/.openinterpreter/config.toml (no `config set`
command exists), keeping everything else the user has in that file.

The starting file is the REAL one from the user's machine right after install.
"""

import tomllib

import pytest

from backend.core.agents import toml_edit
from backend.core.agents.open_interpreter_adapter import (
    API_KEY_ENV,
    BACKUP_SUFFIX,
    LOCAL_MODEL_PLACEHOLDER,
    PROVIDER_ID,
    OpenInterpreterAdapter,
)
from backend.core.agents.verification import verify_settings

REAL_FRESH_CONFIG = "[tui]\nscreen_reader_detection_done = true\n"
URL = "http://127.0.0.1:8000/v1"


def _adapter(monkeypatch, tmp_path, initial=None):
    cfg = tmp_path / ".openinterpreter" / "config.toml"
    if initial is not None:
        cfg.parent.mkdir(parents=True)
        cfg.write_text(initial, encoding="utf-8")
    a = OpenInterpreterAdapter()
    monkeypatch.setattr(a, "_config_path", lambda: cfg)
    monkeypatch.setattr(a, "_executable", lambda: "/usr/bin/interpreter")
    return a, cfg


def test_writes_provider_and_keeps_the_users_other_settings(monkeypatch, tmp_path):
    a, cfg = _adapter(monkeypatch, tmp_path, REAL_FRESH_CONFIG)
    a.configure(URL, "sk-no-key", "Qwen3.5-9B-Q4_K_M")
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["tui"] == {"screen_reader_detection_done": True}  # untouched
    assert data["model_provider"] == PROVIDER_ID
    assert data["model"] == "Qwen3.5-9B-Q4_K_M"
    p = data["model_providers"][PROVIDER_ID]
    assert p["base_url"] == URL and p["wire_api"] == "chat" and p["env_key"] == API_KEY_ENV


def test_the_api_key_is_never_written_to_the_file(monkeypatch, tmp_path):
    a, cfg = _adapter(monkeypatch, tmp_path, REAL_FRESH_CONFIG)
    a.configure(URL, "sk-SECRET-123", "m")
    assert "sk-SECRET-123" not in cfg.read_text(encoding="utf-8")
    assert a.launch_env("sk-SECRET-123") == {API_KEY_ENV: "sk-SECRET-123"}


def test_creates_the_file_when_it_does_not_exist(monkeypatch, tmp_path):
    a, cfg = _adapter(monkeypatch, tmp_path)
    a.configure(URL, "k", None)
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["model"] == LOCAL_MODEL_PLACEHOLDER


def test_backup_is_made_once_and_holds_the_original(monkeypatch, tmp_path):
    a, cfg = _adapter(monkeypatch, tmp_path, REAL_FRESH_CONFIG)
    a.configure(URL, "k", "m1")
    backup = cfg.with_name(cfg.name + BACKUP_SUFFIX)
    assert backup.read_text(encoding="utf-8") == REAL_FRESH_CONFIG
    a.configure(URL, "k", "m2")  # second run must NOT overwrite the original backup
    assert backup.read_text(encoding="utf-8") == REAL_FRESH_CONFIG


def test_configure_is_idempotent(monkeypatch, tmp_path):
    a, cfg = _adapter(monkeypatch, tmp_path, REAL_FRESH_CONFIG)
    a.configure(URL, "k", "m")
    first = cfg.read_text(encoding="utf-8")
    a.configure(URL, "k", "m")
    assert cfg.read_text(encoding="utf-8") == first


def test_changing_the_runtime_replaces_the_old_values(monkeypatch, tmp_path):
    a, cfg = _adapter(monkeypatch, tmp_path, REAL_FRESH_CONFIG)
    a.configure("http://127.0.0.1:8000/v1", "k", "old-model")
    a.configure("http://127.0.0.1:9001/v1", "k", "new-model")
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["model"] == "new-model"
    assert data["model_providers"][PROVIDER_ID]["base_url"] == "http://127.0.0.1:9001/v1"


def test_a_cloud_model_already_set_is_overridden(monkeypatch, tmp_path):
    a, cfg = _adapter(monkeypatch, tmp_path, 'model = "gpt-5.1-codex"\nmodel_provider = "openai"\n')
    a.configure(URL, "k", "qwen")
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["model"] == "qwen" and data["model_provider"] == PROVIDER_ID


def test_other_providers_the_user_defined_survive(monkeypatch, tmp_path):
    initial = '[model_providers.acme]\nname = "Acme"\nbase_url = "https://a/v1"\nenv_key = "ACME"\nwire_api = "responses"\n'
    a, cfg = _adapter(monkeypatch, tmp_path, initial)
    a.configure(URL, "k", "m")
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["model_providers"]["acme"]["env_key"] == "ACME"
    assert PROVIDER_ID in data["model_providers"]


def test_a_file_we_cannot_rewrite_faithfully_is_left_untouched(monkeypatch, tmp_path):
    weird = "[[servers]]\nname = 'a'\n\n[[servers]]\nname = 'b'\n"
    a, cfg = _adapter(monkeypatch, tmp_path, weird)
    with pytest.raises(RuntimeError, match="دست نخورده"):
        a.configure(URL, "k", "m")
    assert cfg.read_text(encoding="utf-8") == weird


def test_unreadable_toml_is_reported_not_overwritten(monkeypatch, tmp_path):
    a, cfg = _adapter(monkeypatch, tmp_path, "this is = = not toml")
    with pytest.raises(RuntimeError):
        a.configure(URL, "k", "m")
    assert cfg.read_text(encoding="utf-8") == "this is = = not toml"


def test_not_installed_gives_a_clear_error(monkeypatch, tmp_path):
    a, _ = _adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(a, "_executable", lambda: None)
    with pytest.raises(RuntimeError, match="Open Interpreter"):
        a.configure(URL, "k", "m")


def test_read_back_verification_passes_after_configure(monkeypatch, tmp_path):
    a, _ = _adapter(monkeypatch, tmp_path, REAL_FRESH_CONFIG)
    a.configure(URL, "k", "qwen")
    report = verify_settings(a, a.expected_settings(URL, "k", "qwen"))
    assert report.verified and report.ok, report.to_dict()


def test_read_back_verification_catches_a_stale_file(monkeypatch, tmp_path):
    a, cfg = _adapter(monkeypatch, tmp_path, REAL_FRESH_CONFIG)
    a.configure(URL, "k", "qwen")
    cfg.write_text('model = "gpt-5.1-codex"\n', encoding="utf-8")  # someone/something reverted it
    report = verify_settings(a, a.expected_settings(URL, "k", "qwen"))
    assert not report.ok
    assert any(c.key == "model" and c.status == "mismatch" for c in report.checks)


def test_launch_command_is_the_real_cli(monkeypatch, tmp_path):
    a, _ = _adapter(monkeypatch, tmp_path)
    assert a.build_launch_command() == ["/usr/bin/interpreter"]


# ---- the TOML writer itself ----

@pytest.mark.parametrize(
    "doc",
    [
        {"a": 1, "b": "x y", "c": True, "d": 1.5, "e": ["p", "q"]},
        {"top": "v", "t": {"k": 1, "sub": {"z": "deep"}}},
        {"quoted.key": 1, "t": {"we ird": "é ü \"quote\" \\ back"}},
        {"only": {"nested": {"x": 1}}},
        {},
    ],
)
def test_dumps_round_trips_exactly(doc):
    assert tomllib.loads(toml_edit.dumps(doc)) == doc


def test_top_level_scalars_are_written_before_any_table():
    text = toml_edit.dumps({"t": {"k": 1}, "model": "m"})
    assert text.index("model") < text.index("[t]")


def test_unsupported_structures_are_refused():
    with pytest.raises(toml_edit.UnsupportedToml):
        toml_edit.dumps({"s": [{"a": 1}]})


def test_a_value_that_would_produce_invalid_toml_is_refused_not_written():
    with pytest.raises(toml_edit.UnsupportedToml):
        toml_edit.dumps({"k": "bad\x7fchar"})
