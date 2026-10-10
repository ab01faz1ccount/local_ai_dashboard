"""
tests/backend/test_hermes_adapter_configure.py

Regression: after "Configure & Connect" the Hermes banner still showed the
stock cloud model (claude-opus-4.6). The adapter used to write `model.name`,
which Hermes doesn't document; its model-id key is `model.default`.

Uses a real subprocess: a tiny fake `hermes` executable (a Python script)
that records every `config set` it receives, so the argv the adapter
actually produces is what's asserted -- not a mocked subprocess.run.
"""

import json
import os
import stat
import sys

from backend.core.agents.hermes_adapter import LOCAL_MODEL_PLACEHOLDER, HermesAdapter


def _fake_hermes(tmp_path):
    log = tmp_path / "calls.jsonl"
    script = tmp_path / "hermes"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script), log


def _sets(log):
    out = {}
    for line in log.read_text().splitlines():
        argv = json.loads(line)
        assert argv[:2] == ["config", "set"], argv
        out[argv[2]] = argv[3]
    return out


def _adapter(monkeypatch, exe):
    adapter = HermesAdapter()
    monkeypatch.setattr(adapter, "_executable", lambda: exe)
    return adapter


def test_points_hermes_at_runtime_and_overrides_the_cloud_model(tmp_path, monkeypatch):
    exe, log = _fake_hermes(tmp_path)
    _adapter(monkeypatch, exe).configure("http://127.0.0.1:8080/v1", "sk-no-key", "qwen2.5-7b")
    s = _sets(log)
    assert s["model.provider"] == "custom"
    assert s["model.base_url"] == "http://127.0.0.1:8080/v1"
    assert s["model.default"] == "qwen2.5-7b"  # the documented key
    assert s["model.api_key"] == "sk-no-key"
    assert "model.name" not in s


def test_model_default_is_always_overwritten_even_without_a_model_name(tmp_path, monkeypatch):
    # No registered model on the runtime must NOT leave the cloud default behind.
    exe, log = _fake_hermes(tmp_path)
    _adapter(monkeypatch, exe).configure("http://127.0.0.1:8080/v1", "sk-no-key", None)
    assert _sets(log)["model.default"] == LOCAL_MODEL_PLACEHOLDER


def test_returns_every_command_it_ran(tmp_path, monkeypatch):
    exe, _ = _fake_hermes(tmp_path)
    ran = _adapter(monkeypatch, exe).configure("http://127.0.0.1:8080/v1", "k", "m")
    assert any("model.default m" in r for r in ran)
    assert len(ran) == 5


# ---- model.context_length: tell Hermes the window the server really has ----

def test_context_length_is_written_when_known(tmp_path, monkeypatch):
    exe, log = _fake_hermes(tmp_path)
    _adapter(monkeypatch, exe).configure("http://127.0.0.1:8000/v1", "k", "qwen", 64000)
    assert _sets(log)["model.context_length"] == "64000"


def test_context_length_is_left_alone_when_unknown(tmp_path, monkeypatch):
    exe, log = _fake_hermes(tmp_path)
    _adapter(monkeypatch, exe).configure("http://127.0.0.1:8000/v1", "k", "qwen", None)
    assert "model.context_length" not in _sets(log)


def test_expected_settings_includes_it_so_it_gets_verified_too():
    s = HermesAdapter().expected_settings("http://h/v1", "k", "m", 16384)
    assert s["model.context_length"] == "16384"
    assert "model.context_length" not in HermesAdapter().expected_settings("http://h/v1", "k", "m")
