"""
tests/backend/test_llama_cpp_args.py

LlamaCppEngine._build_args: config dict -> llama-server flags. Includes
the --jinja flag that agent tool calling depends on (a runtime launched
without it can't be given tools).
"""

import pytest

from backend.core.engine.llama_cpp_engine import LlamaCppEngine


@pytest.fixture
def engine():
    return LlamaCppEngine(platform_provider=None, executable_path="/bin/llama-server")  # type: ignore[arg-type]


def test_jinja_flag_only_when_enabled(engine):
    assert "--jinja" not in engine._build_args({"model_path": "/m.gguf"})
    assert "--jinja" not in engine._build_args({"model_path": "/m.gguf", "jinja": False})
    assert "--jinja" in engine._build_args({"model_path": "/m.gguf", "jinja": True})


def test_jinja_is_a_bare_flag_not_a_key_value_pair(engine):
    args = engine._build_args({"jinja": True, "ctx_size": 4096})
    assert args.count("--jinja") == 1 and "True" not in args
    assert args[args.index("--ctx-size") + 1] == "4096"


def test_model_host_port_and_ctx(engine):
    args = engine._build_args({"model_path": "/m.gguf", "host": "127.0.0.1", "port": 8080, "ctx_size": 8192})
    assert args[:2] == ["--model", "/m.gguf"]
    for flag, val in [("--host", "127.0.0.1"), ("--port", "8080"), ("--ctx-size", "8192")]:
        assert args[args.index(flag) + 1] == val


def test_unset_and_none_values_are_omitted(engine):
    args = engine._build_args({"ctx_size": None, "threads": None})
    assert "--ctx-size" not in args and "--threads" not in args


@pytest.mark.parametrize("key,flag", [("flash_attn", "--flash-attn"), ("mlock", "--mlock"), ("no_mmap", "--no-mmap"), ("embedding", "--embedding")])
def test_boolean_flags(engine, key, flag):
    assert flag not in engine._build_args({key: False})
    assert flag in engine._build_args({key: True})


def test_extra_args_are_appended_last(engine):
    args = engine._build_args({"jinja": True, "extra_args": ["--no-webui", "--slots"]})
    assert args[-2:] == ["--no-webui", "--slots"]


def test_empty_config_is_an_empty_command(engine):
    assert engine._build_args({}) == []
