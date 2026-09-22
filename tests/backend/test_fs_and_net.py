import os
import stat
import urllib.error
import pytest
from backend.core import fs_browser
from backend.core.net_util import host_allowed, _RestrictedRedirectHandler


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "a.gguf").write_bytes(b"x" * 10)
    (tmp_path / "models" / "b.txt").write_text("hi")
    (tmp_path / "models" / ".hidden.gguf").write_bytes(b"x")
    (tmp_path / "models" / "sub").mkdir()
    exe = tmp_path / "models" / "run.sh"
    exe.write_text("#!/bin/sh\necho hi\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return tmp_path / "models"


def names(res):
    return [e["name"] for e in res["entries"]]


def test_lists_dirs_first_and_hides_dotfiles(tree):
    res = fs_browser.list_directory(str(tree))
    assert names(res) == ["sub", "a.gguf", "b.txt", "run.sh"]
    assert res["entries"][0]["is_dir"] and res["entries"][0]["size"] is None
    assert res["entries"][1]["size"] == 10


def test_show_hidden(tree):
    assert ".hidden.gguf" in names(fs_browser.list_directory(str(tree), show_hidden=True))


def test_extension_filter_keeps_folders(tree):
    res = fs_browser.list_directory(str(tree), kind="file", extensions=["gguf"])
    assert names(res) == ["sub", "a.gguf"]


def test_dir_kind_hides_files(tree):
    assert names(fs_browser.list_directory(str(tree), kind="dir")) == ["sub"]


@pytest.mark.skipif(os.name == "nt", reason="exec bit is POSIX")
def test_executable_only(tree):
    assert names(fs_browser.list_directory(str(tree), executable_only=True)) == ["sub", "run.sh"]


def test_file_path_shows_its_folder_and_quotes_are_stripped(tree):
    res = fs_browser.list_directory(f'"{tree / "a.gguf"}"')
    assert res["path"] == str(tree.resolve())
    assert res["parent"] == str(tree.resolve().parent)


def test_bad_path_and_bad_kind(tmp_path):
    with pytest.raises(fs_browser.FsBrowseError):
        fs_browser.list_directory(str(tmp_path / "nope"))
    with pytest.raises(fs_browser.FsBrowseError):
        fs_browser.list_directory(str(tmp_path), kind="weird")


def test_posix_root_has_no_parent():
    if os.name == "nt":
        pytest.skip("posix only")
    res = fs_browser.list_directory("/")
    assert res["parent"] is None


def test_default_is_home_and_roots_present():
    res = fs_browser.list_directory(None)
    assert res["path"] == str(fs_browser.Path.home().resolve())
    assert any(r["name"] == "Home" for r in res["roots"])


@pytest.mark.skipif(os.name == "nt", reason="chmod semantics")
def test_permission_denied(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running as root; can't test permission denial")
        with pytest.raises(fs_browser.FsBrowseError):
            fs_browser.list_directory(str(locked))
    finally:
        locked.chmod(0o700)


def test_broken_symlink_is_skipped(tree):
    if os.name == "nt":
        pytest.skip("symlinks need privileges on Windows")
    os.symlink(tree / "missing", tree / "broken")
    assert "broken" not in names(fs_browser.list_directory(str(tree)))


def test_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr(fs_browser, "MAX_ENTRIES", 3)
    for i in range(6):
        (tmp_path / f"f{i}.gguf").write_bytes(b"x")
    res = fs_browser.list_directory(str(tmp_path))
    assert res["truncated"] and len(res["entries"]) == 3


# ---- net_util ----
HF = ("huggingface.co", "hf.co")

@pytest.mark.parametrize("url,ok", [
    ("https://huggingface.co/api/models", True),
    ("https://cdn-lfs.hf.co/repos/x", True),
    ("https://cas-bridge.xethub.hf.co/x", True),
    ("http://huggingface.co/x", False),          # not https
    ("https://evilhuggingface.co/x", False),     # suffix trick
    ("https://huggingface.co.evil.com/x", False),
    ("https://example.com/x", False),
    ("https://", False),
])
def test_host_allowed(url, ok):
    assert host_allowed(url, HF) is ok


def test_redirect_to_other_host_is_blocked():
    h = _RestrictedRedirectHandler(HF)
    import urllib.request
    req = urllib.request.Request("https://huggingface.co/a")
    with pytest.raises(urllib.error.URLError):
        h.redirect_request(req, None, 302, "Found", {}, "https://evil.example.com/steal")
    ok = h.redirect_request(req, None, 302, "Found", {}, "https://cdn-lfs.hf.co/file")
    assert ok is not None
