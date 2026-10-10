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


# ---------------- flash attention: syntax depends on the llama.cpp build ----------------

from backend.core.engine import llama_cpp_engine as engine_module  # noqa: E402


def _fa(monkeypatch, takes_value):
    monkeypatch.setattr(engine_module, "flash_attn_takes_value", lambda path: takes_value)


@pytest.mark.parametrize(
    "value,expected",
    [(True, "on"), ("on", "on"), ("true", "on"), ("auto", "auto"), ("off", "off"), (" ON ", "on")],
)
def test_flash_attn_new_builds_get_an_explicit_value(engine, monkeypatch, value, expected):
    _fa(monkeypatch, True)
    args = engine._build_args({"flash_attn": value})
    assert args[args.index("--flash-attn") + 1] == expected


@pytest.mark.parametrize("value", [False, None, "", "bogus"])
def test_flash_attn_unset_passes_nothing(engine, monkeypatch, value):
    _fa(monkeypatch, True)
    assert "--flash-attn" not in engine._build_args({"flash_attn": value})


def test_flash_attn_value_never_swallows_the_next_flag(engine, monkeypatch):
    _fa(monkeypatch, True)
    args = engine._build_args({"flash_attn": True, "mlock": True})
    i = args.index("--flash-attn")
    assert args[i : i + 3] == ["--flash-attn", "on", "--mlock"]


def test_flash_attn_old_builds_get_a_bare_switch_only_for_on(engine, monkeypatch):
    _fa(monkeypatch, False)
    on = engine._build_args({"flash_attn": "on", "mlock": True})
    assert on[on.index("--flash-attn") + 1] == "--mlock"  # bare: no value after it
    assert "--flash-attn" in engine._build_args({"flash_attn": True})
    assert "--flash-attn" not in engine._build_args({"flash_attn": "off"})
    assert "--flash-attn" not in engine._build_args({"flash_attn": "auto"})


def test_flash_attn_probe_is_skipped_when_unset(engine, monkeypatch):
    def boom(path):
        raise AssertionError("must not run the binary when flash_attn is unset")

    monkeypatch.setattr(engine_module, "flash_attn_takes_value", boom)
    engine._build_args({"ctx_size": 4096})


# ---------------- KV offload: tri-state, only an explicit "off" disables it ----------------

@pytest.mark.parametrize("value", [None, "", "auto", "bogus"])
def test_kv_offload_unset_passes_nothing(engine, value):
    args = engine._build_args({"kv_offload": value})
    assert "--kv-offload" not in args and "--no-kv-offload" not in args


@pytest.mark.parametrize("value", [True, "on", "true"])
def test_kv_offload_on(engine, value):
    args = engine._build_args({"kv_offload": value})
    assert "--kv-offload" in args and "--no-kv-offload" not in args


@pytest.mark.parametrize("value", [False, "off", "false"])
def test_kv_offload_off_is_explicit(engine, value):
    args = engine._build_args({"kv_offload": value})
    assert "--no-kv-offload" in args and "--kv-offload" not in args


def test_kv_offload_off_string_is_not_treated_as_truthy(engine):
    # the old code did `"--kv-offload" if value else ...`: the string "off" is truthy
    assert "--kv-offload" not in engine._build_args({"kv_offload": "off"})


# ---------------- extra args ----------------

def test_extra_args_from_a_multiline_string_one_argv_entry_per_line(engine):
    text = "--reasoning\noff\n\n  --chat-template-kwargs  \n{\"enable_thinking\":false}\n"
    assert engine._build_args({"extra_args": text}) == [
        "--reasoning",
        "off",
        "--chat-template-kwargs",
        '{"enable_thinking":false}',
    ]


def test_extra_args_list_blank_entries_dropped_and_values_stringified(engine):
    assert engine._build_args({"extra_args": ["--a", "", "  ", 5]}) == ["--a", "5"]


@pytest.mark.parametrize("value", [None, "", "\n \n", []])
def test_extra_args_empty_forms_add_nothing(engine, value):
    assert engine._build_args({"extra_args": value}) == []


def test_extra_args_stay_last_even_with_flash_attn(engine, monkeypatch):
    _fa(monkeypatch, True)
    args = engine._build_args({"flash_attn": "on", "extra_args": "--x\n1"})
    assert args[-2:] == ["--x", "1"]


# ---- the engine remembers exactly what it launched (feeds runtime_inspect) ----

class _RecordingPlatform:
    def __init__(self):
        self.started = None

    def start_process(self, exe, args):
        self.started = (exe, args)
        return 4242

    def is_process_running(self, pid):
        return True


def test_engine_records_the_exact_launch_command(monkeypatch):
    plat = _RecordingPlatform()
    eng = LlamaCppEngine(plat, "/bin/llama-server")  # type: ignore[arg-type]
    monkeypatch.setattr(eng, "_wait_for_health", lambda timeout: True)
    assert eng.last_command() is None
    eng.start({"model_path": "/m.gguf", "port": 8000, "n_gpu_layers": 99, "ctx_size": 16384})
    cmd = eng.last_command()
    assert cmd[0] == "/bin/llama-server"
    assert cmd[cmd.index("--n-gpu-layers") + 1] == "99"
    assert cmd == [plat.started[0], *plat.started[1]]  # identical to what the OS was given
    assert eng.last_config()["n_gpu_layers"] == 99


def test_fetch_props_is_none_when_offline(engine):
    assert engine.fetch_props() is None
