"""
tests/backend/test_agent_verification.py

"Did configure() actually take effect?" -- core/agents/verification.py.

Two layers:
  * the generic verifier against a small in-test adapter (every status and
    edge: lenient matching, cloud leftovers, unreadable, sensitive values);
  * the real HermesAdapter against a real subprocess: a stateful fake
    `hermes` script that stores `config set` values in a JSON file and prints
    them for `config get` -- including a variant that SILENTLY IGNORES a key,
    which is precisely the `model.name` bug this layer exists to catch.
"""

import json
import stat
import sys

from backend.core.agents.base import AgentAdapter
from backend.core.agents.hermes_adapter import HermesAdapter
from backend.core.agents.verification import looks_like_cloud_model, value_matches, verify_settings


class _StubAdapter(AgentAdapter):
    backend_id = "stub"
    display_name = "Stub"
    brand_color = "#000"
    sensitive_settings = frozenset({"api_key"})

    def __init__(self, values, unreadable=()):
        self.values = values
        self.unreadable = set(unreadable)
        self.read_keys = []

    def detect(self):
        raise NotImplementedError

    def configure(self, *a, **k):
        raise NotImplementedError

    def build_launch_command(self, resume_session_id=None):
        raise NotImplementedError

    def read_setting(self, key):
        self.read_keys.append(key)
        if key in self.unreadable:
            raise RuntimeError("cannot read")
        return self.values.get(key)


def _status(report):
    return {c.key: c.status for c in report.checks}


# ---------- generic verifier ----------

def test_all_ok_is_verified():
    a = _StubAdapter({"model": "qwen", "url": "http://127.0.0.1:8080/v1"})
    r = verify_settings(a, {"model": "qwen", "url": "http://127.0.0.1:8080/v1"})
    assert _status(r) == {"model": "ok", "url": "ok"}
    assert r.ok and r.verified


def test_matching_ignores_case_quotes_and_key_value_framing():
    assert value_matches("custom", "Custom")
    assert value_matches("custom", '"custom"')
    assert value_matches("qwen2.5-7b", "model.default: 'qwen2.5-7b'\n")
    assert not value_matches("qwen", "llama")
    assert not value_matches("qwen", None)


def test_stale_cloud_default_is_a_mismatch_and_called_out():
    a = _StubAdapter({"model.default": "Anthropic/claude-opus-4.6"})
    r = verify_settings(a, {"model.default": "qwen2.5-7b"})
    (m,) = r.mismatches
    assert m.key == "model.default" and m.actual == "Anthropic/claude-opus-4.6"
    assert "cloud" in m.detail
    assert not r.ok and not r.verified


def test_mismatch_that_is_not_cloud_has_no_cloud_note():
    r = verify_settings(_StubAdapter({"url": "http://127.0.0.1:8000/v1"}), {"url": "http://127.0.0.1:8080/v1"})
    assert r.mismatches and "cloud" not in r.mismatches[0].detail


def test_unreadable_is_unknown_not_failure():
    a = _StubAdapter({}, unreadable={"model", "url"})
    r = verify_settings(a, {"model": "x", "url": "y"})
    assert _status(r) == {"model": "unreadable", "url": "unreadable"}
    assert r.ok  # nothing contradicted...
    assert not r.verified  # ...but nothing confirmed either


def test_one_confirmed_plus_unreadable_is_verified():
    a = _StubAdapter({"model": "x"}, unreadable={"url"})
    r = verify_settings(a, {"model": "x", "url": "y"})
    assert r.ok and r.verified


def test_default_adapter_without_read_support_reports_unreadable():
    class NoRead(_StubAdapter):
        def read_setting(self, key):
            return AgentAdapter.read_setting(self, key)

    r = verify_settings(NoRead({}), {"model": "x"})
    assert _status(r) == {"model": "unreadable"}


def test_sensitive_values_are_never_read_or_exposed():
    a = _StubAdapter({"api_key": "sk-secret-123", "model": "x"})
    r = verify_settings(a, {"api_key": "sk-secret-123", "model": "x"})
    assert "api_key" not in a.read_keys
    assert _status(r)["api_key"] == "skipped"
    assert "sk-secret-123" not in json.dumps(r.to_dict())


def test_cloud_model_heuristic():
    for v in ["claude-opus-4.6", "Anthropic/claude-sonnet", "gpt-4o", "openai/gpt-5", "gemini-2.5-pro"]:
        assert looks_like_cloud_model(v), v
    for v in ["qwen2.5-7b-instruct", "llama-3.1-8b", "local-model", "", None]:
        assert not looks_like_cloud_model(v), v


def test_report_to_dict_shape():
    d = verify_settings(_StubAdapter({"m": "x"}), {"m": "x"}).to_dict()
    assert d["backend_id"] == "stub" and d["ok"] is True and d["verified"] is True
    assert d["checks"][0] == {"key": "m", "status": "ok", "expected": "x", "actual": "x", "detail": None}


# ---------- real HermesAdapter against a stateful fake `hermes` ----------

def _fake_hermes(tmp_path, ignore=(), seed=None):
    """A real executable. `config set K V` stores K->V in a JSON file --
    except for keys in `ignore`, which it accepts (exit 0) and drops, like an
    agent that doesn't know that key. `config get K` prints `K: V`."""
    store = tmp_path / "store.json"
    store.write_text(json.dumps({"model.default": "Anthropic/claude-opus-4.6", **(seed or {})}))
    script = tmp_path / "hermes"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"store = {str(store)!r}\n"
        f"ignore = set({list(ignore)!r})\n"
        "a = sys.argv[1:]\n"
        "d = json.load(open(store))\n"
        "if a[:2] == ['config', 'set']:\n"
        "    if a[2] not in ignore:\n"
        "        d[a[2]] = a[3]\n"
        "        json.dump(d, open(store, 'w'))\n"
        "elif a[:2] == ['config', 'get']:\n"
        "    if a[2] not in d:\n"
        "        sys.stderr.write('not set'); sys.exit(1)\n"
        "    print(a[2] + ': ' + d[a[2]])\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def _hermes(monkeypatch, exe):
    adapter = HermesAdapter()
    monkeypatch.setattr(adapter, "_executable", lambda: exe)
    return adapter


URL = "http://127.0.0.1:8080/v1"


def test_hermes_configure_then_verify_confirms_everything(tmp_path, monkeypatch):
    adapter = _hermes(monkeypatch, _fake_hermes(tmp_path))
    adapter.configure(URL, "sk-no-key", "qwen2.5-7b")
    r = verify_settings(adapter, adapter.expected_settings(URL, "sk-no-key", "qwen2.5-7b"))
    assert r.ok and r.verified
    assert _status(r) == {
        "model.provider": "ok",
        "model.base_url": "ok",
        "model.default": "ok",
        "model.api_key": "skipped",
        "LLAMA_CPP_API_KEY": "skipped",
    }


def test_hermes_that_silently_ignores_the_model_key_is_caught(tmp_path, monkeypatch):
    # The original bug: the write "succeeds" but the stock cloud model stays.
    adapter = _hermes(monkeypatch, _fake_hermes(tmp_path, ignore={"model.default"}))
    adapter.configure(URL, "sk-no-key", "qwen2.5-7b")  # exits 0 -- no error raised
    r = verify_settings(adapter, adapter.expected_settings(URL, "sk-no-key", "qwen2.5-7b"))
    (m,) = r.mismatches
    assert m.key == "model.default"
    assert "claude-opus" in m.actual and "cloud" in m.detail
    assert not r.ok


def test_hermes_wrong_port_is_caught(tmp_path, monkeypatch):
    # "runtime doesn't match": hermes keeps pointing at another port (8000)
    # because it ignored our base_url write.
    adapter = _hermes(
        monkeypatch,
        _fake_hermes(tmp_path, ignore={"model.base_url"}, seed={"model.base_url": "http://127.0.0.1:8000/v1"}),
    )
    adapter.configure(URL, "k", "m")
    r = verify_settings(adapter, adapter.expected_settings(URL, "k", "m"))
    (m,) = r.mismatches
    assert m.key == "model.base_url" and m.actual.endswith(":8000/v1") and m.expected == URL


def test_hermes_setting_that_was_never_stored_is_unknown_not_ok(tmp_path, monkeypatch):
    # `config get` exits non-zero for a missing key -> we can't tell -> unreadable.
    adapter = _hermes(monkeypatch, _fake_hermes(tmp_path, ignore={"model.base_url"}))
    adapter.configure(URL, "k", "m")
    r = verify_settings(adapter, adapter.expected_settings(URL, "k", "m"))
    assert _status(r)["model.base_url"] == "unreadable"


def test_hermes_read_setting_when_cli_missing(monkeypatch):
    adapter = HermesAdapter()
    monkeypatch.setattr(adapter, "_executable", lambda: None)
    r = verify_settings(adapter, {"model.default": "x"})
    assert _status(r) == {"model.default": "unreadable"}
