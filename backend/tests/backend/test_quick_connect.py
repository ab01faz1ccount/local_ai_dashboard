"""quick_connect + endpoint: ports, executable choice, the runtime definition, key hygiene."""

from types import SimpleNamespace

import pytest

from backend.core import endpoint, quick_connect as qc

GB = 1024**3
HW = {"memory": SimpleNamespace(total_mb=16384), "gpu": SimpleNamespace(available=True, vram_total_mb=8192)}


def test_next_free_port_skips_used_ones():
    assert qc.next_free_port(set()) == 8080
    assert qc.next_free_port({8080, 8081}) == 8082


def test_no_port_left_is_an_error():
    with pytest.raises(qc.QuickConnectError):
        qc.next_free_port({p for p in range(65534, 65536)}, start=65534)


def test_executable_prefers_the_one_already_in_use_then_the_detected_one():
    assert qc.pick_executable(["", "/a/llama-server"], "/b/llama-server") == "/a/llama-server"
    assert qc.pick_executable([], "/b/llama-server") == "/b/llama-server"


def test_no_executable_anywhere_explains_what_to_do():
    with pytest.raises(qc.QuickConnectError) as e:
        qc.pick_executable([], None)
    assert "llama-server" in str(e.value.args[0])


def test_runtime_definition_uses_hardware_derived_settings_and_a_free_port():
    out = qc.runtime_for_model(
        model_name="m", file_size_bytes=4 * GB, trained_context=None, hardware=HW,
        executable_path="/x/llama-server", used_ports={8080},
    )
    rt = out["runtime"]
    assert rt["port"] == 8081 and rt["host"] == "127.0.0.1" and rt["name"] == "m"
    assert rt["config_json"]["n_gpu_layers"] == 999 and rt["config_json"]["jinja"] is True
    assert out["notes"]


def test_endpoint_for_a_runtime_builds_the_v1_url():
    rt = SimpleNamespace(id=3, host="127.0.0.1", port=8081)
    ep = endpoint.for_runtime(rt, "m", 8192, api_key="sk-secret")
    assert ep.base_url == "http://127.0.0.1:8081/v1" and ep.source_id == 3


def test_the_key_never_appears_in_repr_or_the_public_dict():
    ep = endpoint.Endpoint(base_url="http://h/v1", api_key="sk-secret")
    assert "sk-secret" not in repr(ep)
    pub = ep.public_dict()
    assert "sk-secret" not in str(pub) and pub["has_key"] is True
    assert endpoint.Endpoint(base_url="http://h/v1").public_dict()["has_key"] is False
