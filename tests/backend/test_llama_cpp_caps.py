"""
tests/backend/test_llama_cpp_caps.py

`--flash-attn` is a bare switch on older llama.cpp and takes [on|off|auto] on
newer builds. We ask the installed binary (`--help`) which one it is.
Real subprocess: a tiny executable that prints a help text and counts calls.
"""

import stat
import sys

import pytest

from backend.core.engine import llama_cpp_caps as caps

NEW_HELP = """\
-t,    --threads N                      number of CPU threads
-fa,   --flash-attn [on|off|auto]       set Flash Attention use ('on', 'off', or 'auto', default: 'auto')
-ngl,  --gpu-layers N                   number of layers to store in VRAM
"""
OLD_HELP = """\
-t,    --threads N                      number of CPU threads
-fa,   --flash-attn                     enable Flash Attention (default: disabled)
-ngl,  --gpu-layers N                   number of layers to store in VRAM
"""
NO_FA_HELP = "-t, --threads N   number of CPU threads\n"


@pytest.fixture(autouse=True)
def _clear_cache():
    caps._FLASH_ATTN_CACHE.clear()
    yield
    caps._FLASH_ATTN_CACHE.clear()


def _fake_server(tmp_path, help_text, to="stdout"):
    counter = tmp_path / "calls.txt"
    script = tmp_path / "llama-server"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"open({str(counter)!r}, 'a').write('x')\n"
        f"sys.{to}.write({help_text!r})\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script), counter


def test_parse_new_style():
    assert caps.parse_flash_attn_takes_value(NEW_HELP) is True


def test_parse_old_style():
    assert caps.parse_flash_attn_takes_value(OLD_HELP) is False


def test_parse_no_flash_attn_mentioned():
    assert caps.parse_flash_attn_takes_value(NO_FA_HELP) is None


def test_new_binary_detected_via_real_subprocess(tmp_path):
    exe, _ = _fake_server(tmp_path, NEW_HELP)
    assert caps.flash_attn_takes_value(exe) is True


def test_old_binary_detected_via_real_subprocess(tmp_path):
    exe, _ = _fake_server(tmp_path, OLD_HELP)
    assert caps.flash_attn_takes_value(exe) is False


def test_help_printed_on_stderr_is_read_too(tmp_path):
    exe, _ = _fake_server(tmp_path, OLD_HELP, to="stderr")
    assert caps.flash_attn_takes_value(exe) is False


def test_result_is_cached_per_executable(tmp_path):
    exe, counter = _fake_server(tmp_path, OLD_HELP)
    caps.flash_attn_takes_value(exe)
    caps.flash_attn_takes_value(exe)
    assert counter.read_text() == "x"  # the binary ran once


def test_unprobeable_binary_assumes_new_style_and_is_not_cached(tmp_path):
    missing = str(tmp_path / "nope")
    assert caps.flash_attn_takes_value(missing) is True
    assert missing not in caps._FLASH_ATTN_CACHE


def test_empty_help_output_assumes_new_style_and_is_not_cached(tmp_path):
    exe, _ = _fake_server(tmp_path, "")
    assert caps.flash_attn_takes_value(exe) is True
    assert exe not in caps._FLASH_ATTN_CACHE


def test_help_without_flash_attn_defaults_to_new_style(tmp_path):
    exe, _ = _fake_server(tmp_path, NO_FA_HELP)
    assert caps.flash_attn_takes_value(exe) is True
