import hashlib
import threading
import time
from pathlib import Path

import pytest

from backend.core import model_discovery as md
from backend.core.net_util import DiscoveryError


# ---------- pure helpers ----------

@pytest.mark.parametrize("name,expected", [
    ("Llama-3.2-3B-Instruct-Q4_K_M.gguf", "Q4_K_M"),
    ("model.Q8_0.gguf", "Q8_0"),
    ("qwen2.5-7b-instruct-q5_k_s.gguf", "Q5_K_S"),
    ("foo-IQ4_XS.gguf", "IQ4_XS"),
    ("foo-BF16.gguf", "BF16"),
    ("foo-f16.gguf", "F16"),
    ("sub/dir/Model-Q6_K.gguf", "Q6_K"),
    ("plainname.gguf", None),
    ("MyQ4_K_Mish.gguf", None),  # not a standalone token
])
def test_guess_quantization(name, expected):
    assert md.guess_quantization(name) == expected


@pytest.mark.parametrize("repo,expected", [
    ("bartowski/Llama-3.2-3B-Instruct-GGUF", "3B"),
    ("bartowski/Qwen2.5-7B-Instruct-GGUF", "7B"),
    ("x/Mistral-7B-Instruct-v0.3-GGUF", "7B"),
    ("x/model-0.5B", "0.5B"),
    ("x/Mixtral-8x7B", None),
    ("x/gemma-2-9b-it-GGUF", "9B"),
    ("x/no-size-here", None),
])
def test_guess_param_count(repo, expected):
    assert md.guess_param_count(repo) == expected


def test_query_variants_dedupe_and_order():
    assert md._query_variants(["llama", "3.2", "3b"]) == ["llama 3.2 3b", "llama-3.2-3b", "llama3.23b", "llama"]
    assert md._query_variants(["mistral"]) == ["mistral"]


def test_relevance_ignores_punctuation():
    toks = md._tokens("Llama 3.2 3B")
    assert md._relevance("bartowski/Llama-3.2-3B-Instruct-GGUF", toks) == 1.0
    assert md._relevance("x/Qwen2.5-7B", toks) < 1.0


# ---------- search (network mocked) ----------

def _item(repo, downloads=0, **kw):
    return {"id": repo, "downloads": downloads, "likes": 1, "lastModified": "2026-01-01T00:00:00Z", **kw}


def test_search_ranks_by_relevance_then_downloads_and_filters(monkeypatch):
    calls = []

    def fake_get_json(url, **kw):
        calls.append(url)
        return [
            _item("a/Qwen2.5-7B-GGUF", 900),
            _item("b/Llama-3.2-3B-Instruct-GGUF", 100),
            _item("c/Llama-3.2-3B-GGUF", 500),
            _item("d/private-thing", 9999, private=True),
            _item("bad id with spaces", 5),
            _item("e/Llama-3.2-3B-gated", 50, gated="manual"),
        ]

    monkeypatch.setattr(md, "get_json", fake_get_json)
    res = md.search_models("llama 3.2 3b")
    ids = [r["repo_id"] for r in res]
    assert ids[:2] == ["c/Llama-3.2-3B-GGUF", "b/Llama-3.2-3B-Instruct-GGUF"]  # relevance 1.0, by downloads
    assert "d/private-thing" not in ids and "bad id with spaces" not in ids
    assert next(r for r in res if r["repo_id"].startswith("e/"))["gated"] is True
    assert "filter=gguf" in calls[0] and "search=llama%203.2%203b" in calls[0]


def test_search_widens_only_when_thin(monkeypatch):
    seen = []

    def fake(url, **kw):
        seen.append(url)
        return [] if len(seen) == 1 else [_item("z/Llama-3-8B", 5)]

    monkeypatch.setattr(md, "get_json", fake)
    res = md.search_models("llama 3 8b")
    assert len(seen) >= 2 and res and res[0]["repo_id"] == "z/Llama-3-8B"


def test_search_empty_query():
    with pytest.raises(DiscoveryError) as e:
        md.search_models("   ")
    assert e.value.kind == "bad_input"


# ---------- file listing ----------

def _f(path, size, sha=None):
    e = {"type": "file", "path": path, "size": size}
    if sha:
        e["lfs"] = {"oid": sha, "size": size}
    return e


def test_candidates_group_shards_and_skip_junk(monkeypatch):
    tree = [
        {"type": "directory", "path": "Q4"},
        _f("README.md", 10),
        _f("m-Q4_K_M.gguf", 4000, "a" * 64),
        _f("m-Q8_0.gguf", 8000, "b" * 64),
        _f("mmproj-model-f16.gguf", 300, "c" * 64),
        # complete 3-shard model in a subfolder
        _f("big/x-Q4_K_M-00001-of-00003.gguf", 100, "1" * 64),
        _f("big/x-Q4_K_M-00002-of-00003.gguf", 100, "2" * 64),
        _f("big/x-Q4_K_M-00003-of-00003.gguf", 50, "3" * 64),
        # incomplete: shard 2 of 3 missing
        _f("bad/y-00001-of-00003.gguf", 10, "4" * 64),
        _f("bad/y-00003-of-00003.gguf", 10, "5" * 64),
    ]
    monkeypatch.setattr(md, "get_json", lambda url, **kw: tree)
    cands = md.list_gguf_candidates("o/r")
    by = {c["filename"]: c for c in cands}
    assert set(by) == {"m-Q4_K_M.gguf", "m-Q8_0.gguf", "big/x-Q4_K_M-00001-of-00003.gguf"}
    big = by["big/x-Q4_K_M-00001-of-00003.gguf"]
    assert big["parts"] == 3 and big["size_bytes"] == 250 and big["display_name"] == "x-Q4_K_M"
    assert [f["path"] for f in big["files"]] == [
        "big/x-Q4_K_M-00001-of-00003.gguf",
        "big/x-Q4_K_M-00002-of-00003.gguf",
        "big/x-Q4_K_M-00003-of-00003.gguf",
    ]
    assert by["m-Q4_K_M.gguf"]["quantization"] == "Q4_K_M" and by["m-Q4_K_M.gguf"]["verifiable"] is True
    assert [c["size_bytes"] for c in cands] == sorted(c["size_bytes"] for c in cands)  # smallest first


def test_candidates_reject_bad_repo():
    with pytest.raises(DiscoveryError):
        md.list_gguf_candidates("../../etc/passwd")


def test_find_candidate_missing(monkeypatch):
    monkeypatch.setattr(md, "get_json", lambda url, **kw: [_f("a.gguf", 1)])
    with pytest.raises(DiscoveryError) as e:
        md.find_candidate("o/r", "other.gguf")
    assert e.value.kind == "not_found"


# ---------- downloads (server faked; no network) ----------

class FakeResp:
    def __init__(self, data: bytes, status=200, chunk=64 * 1024, fail_after=None):
        self._data, self.status, self._chunk, self._pos = data, status, chunk, 0
        self._fail_after = fail_after

    def read(self, n=-1):
        if self._fail_after is not None and self._pos >= self._fail_after:
            import http.client
            raise http.client.IncompleteRead(b"")
        end = min(self._pos + min(n, self._chunk), len(self._data))
        if self._fail_after is not None:
            end = min(end, self._fail_after)
        out = self._data[self._pos:end]
        self._pos = end
        return out

    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeServer:
    def __init__(self, blobs: dict, honor_range=True, fail_after=None):
        self.blobs, self.honor_range, self.fail_after = blobs, honor_range, fail_after
        self.requests = []

    def open_url(self, url, *, allowed_hosts, headers=None, timeout=0):
        assert allowed_hosts == md.HF_ALLOWED_HOSTS
        self.requests.append((url, dict(headers or {})))
        name = url.rsplit("/", 1)[-1]
        data = self.blobs[name]
        rng = (headers or {}).get("Range")
        if rng and self.honor_range:
            start = int(rng.split("=")[1].split("-")[0])
            return FakeResp(data[start:], status=206, fail_after=self.fail_after)
        return FakeResp(data, status=200, fail_after=self.fail_after)


def wait(job, timeout=10):
    t0 = time.time()
    while job.status in ("queued", "downloading") and time.time() - t0 < timeout:
        time.sleep(0.02)
    return job


def cand(repo_files, primary=None):
    return {"filename": primary or repo_files[0][0], "files": [
        {"path": p, "size": len(d), "sha256": hashlib.sha256(d).hexdigest()} for p, d in repo_files]}


@pytest.fixture
def mgr():
    return md.DownloadManager()


def test_download_verifies_and_registers(tmp_path, monkeypatch, mgr):
    data = bytes(range(256)) * 5000  # ~1.2MB
    srv = FakeServer({"m-Q4_K_M.gguf": data})
    monkeypatch.setattr(md, "open_url", srv.open_url)
    done = []
    job = mgr.start("o/r", cand([("m-Q4_K_M.gguf", data)]), tmp_path,
                    on_complete=lambda j: done.append(j.local_path) or {"registered": True, "model_id": 7})
    wait(job)
    assert job.status == "done", job.error
    final = tmp_path / "o" / "r" / "m-Q4_K_M.gguf"
    assert final.read_bytes() == data and not final.with_name(final.name + ".part").exists()
    assert job.local_path == str(final) and job.result == {"registered": True, "model_id": 7}
    assert job.downloaded_bytes == len(data) == job.total_bytes
    assert srv.requests[0][0] == "https://huggingface.co/o/r/resolve/main/m-Q4_K_M.gguf"


def test_resume_from_partial(tmp_path, monkeypatch, mgr):
    data = b"abcdefghij" * 100_000
    srv = FakeServer({"m.gguf": data})
    monkeypatch.setattr(md, "open_url", srv.open_url)
    d = tmp_path / "o" / "r"; d.mkdir(parents=True)
    (d / "m.gguf.part").write_bytes(data[:300_000])
    job = wait(mgr.start("o/r", cand([("m.gguf", data)]), tmp_path))
    assert job.status == "done", job.error
    assert (d / "m.gguf").read_bytes() == data
    assert srv.requests[0][1] == {"Range": "bytes=300000-"}
    assert job.downloaded_bytes == len(data)


def test_server_ignoring_range_restarts_cleanly(tmp_path, monkeypatch, mgr):
    data = b"0123456789" * 50_000
    srv = FakeServer({"m.gguf": data}, honor_range=False)
    monkeypatch.setattr(md, "open_url", srv.open_url)
    d = tmp_path / "o" / "r"; d.mkdir(parents=True)
    (d / "m.gguf.part").write_bytes(b"GARBAGE" * 1000)  # wrong prefix that must not survive
    job = wait(mgr.start("o/r", cand([("m.gguf", data)]), tmp_path))
    assert job.status == "done", job.error
    assert (d / "m.gguf").read_bytes() == data
    assert job.downloaded_bytes == len(data)


def test_hash_mismatch_deletes_file(tmp_path, monkeypatch, mgr):
    good = b"a" * 100_000
    evil = b"b" * 100_000  # same size, different content
    srv = FakeServer({"m.gguf": evil})
    monkeypatch.setattr(md, "open_url", srv.open_url)
    job = wait(mgr.start("o/r", cand([("m.gguf", good)]), tmp_path))
    assert job.status == "error" and "SHA256" in job.error
    d = tmp_path / "o" / "r"
    assert not (d / "m.gguf").exists() and not (d / "m.gguf.part").exists()


def test_truncated_download_keeps_part_for_resume(tmp_path, monkeypatch, mgr):
    data = b"z" * 400_000
    srv = FakeServer({"m.gguf": data}, fail_after=150_000)
    monkeypatch.setattr(md, "open_url", srv.open_url)
    job = wait(mgr.start("o/r", cand([("m.gguf", data)]), tmp_path))
    assert job.status == "error"
    part = tmp_path / "o" / "r" / "m.gguf.part"
    assert part.exists() and 0 < part.stat().st_size < len(data)
    # ...and a retry against a healthy server finishes from there.
    srv2 = FakeServer({"m.gguf": data})
    monkeypatch.setattr(md, "open_url", srv2.open_url)
    job2 = wait(mgr.start("o/r", cand([("m.gguf", data)]), tmp_path))
    assert job2.status == "done", job2.error
    assert (tmp_path / "o" / "r" / "m.gguf").read_bytes() == data


def test_split_model_downloads_all_parts(tmp_path, monkeypatch, mgr):
    p1, p2 = b"1" * 50_000, b"2" * 30_000
    srv = FakeServer({"x-00001-of-00002.gguf": p1, "x-00002-of-00002.gguf": p2})
    monkeypatch.setattr(md, "open_url", srv.open_url)
    c = cand([("big/x-00001-of-00002.gguf", p1), ("big/x-00002-of-00002.gguf", p2)])
    job = wait(mgr.start("o/r", c, tmp_path))
    assert job.status == "done", job.error
    d = tmp_path / "o" / "r"
    assert (d / "x-00001-of-00002.gguf").read_bytes() == p1 and (d / "x-00002-of-00002.gguf").read_bytes() == p2
    assert job.local_path == str(d / "x-00001-of-00002.gguf")
    assert job.total_bytes == 80_000 == job.downloaded_bytes


def test_already_downloaded_file_is_skipped(tmp_path, monkeypatch, mgr):
    data = b"q" * 10_000
    srv = FakeServer({"m.gguf": data})
    monkeypatch.setattr(md, "open_url", srv.open_url)
    d = tmp_path / "o" / "r"; d.mkdir(parents=True)
    (d / "m.gguf").write_bytes(data)
    job = wait(mgr.start("o/r", cand([("m.gguf", data)]), tmp_path))
    assert job.status == "done" and srv.requests == []


def test_disk_space_check(tmp_path, monkeypatch, mgr):
    data = b"x" * 1000
    monkeypatch.setattr(md, "open_url", FakeServer({"m.gguf": data}).open_url)
    from collections import namedtuple
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(md.shutil, "disk_usage", lambda p: Usage(10**9, 10**9 - 100, 100))
    job = wait(mgr.start("o/r", cand([("m.gguf", data)]), tmp_path))
    assert job.status == "error" and "فضای دیسک" in job.error


def test_cancel(tmp_path, monkeypatch, mgr):
    data = b"c" * 5_000_000
    class Slow(FakeResp):
        def read(self, n=-1):
            time.sleep(0.01)
            return super().read(n)
    class SlowServer(FakeServer):
        def open_url(self, url, **kw):
            r = super().open_url(url, **kw)
            r.__class__ = Slow
            r._chunk = 16 * 1024
            return r
    srv = SlowServer({"m.gguf": data})
    monkeypatch.setattr(md, "open_url", srv.open_url)
    job = mgr.start("o/r", cand([("m.gguf", data)]), tmp_path)
    time.sleep(0.15)
    assert mgr.cancel(job.id) is True
    wait(job)
    assert job.status == "cancelled"
    assert (tmp_path / "o" / "r" / "m.gguf.part").exists()  # kept so it can resume
    assert mgr.cancel(job.id) is False  # already finished


def test_same_file_twice_returns_same_active_job(tmp_path, monkeypatch, mgr):
    data = b"d" * 3_000_000
    class Slow(FakeResp):
        def read(self, n=-1):
            time.sleep(0.02)
            return super().read(n)
    class S(FakeServer):
        def open_url(self, url, **kw):
            r = super().open_url(url, **kw); r.__class__ = Slow; r._chunk = 32 * 1024; return r
    monkeypatch.setattr(md, "open_url", S({"m.gguf": data}).open_url)
    c = cand([("m.gguf", data)])
    j1 = mgr.start("o/r", c, tmp_path)
    j2 = mgr.start("o/r", c, tmp_path)
    assert j1 is j2
    mgr.cancel(j1.id); wait(j1)


def test_callback_failure_does_not_lose_the_download(tmp_path, monkeypatch, mgr):
    data = b"e" * 2000
    monkeypatch.setattr(md, "open_url", FakeServer({"m.gguf": data}).open_url)
    def boom(job): raise RuntimeError("db locked")
    job = wait(mgr.start("o/r", cand([("m.gguf", data)]), tmp_path, on_complete=boom))
    assert job.status == "done" and job.result == {"registered": False, "register_error": "db locked"}
    assert Path(job.local_path).exists()


def test_done_is_never_visible_before_registration_finishes(tmp_path, monkeypatch, mgr):
    """Regression: status used to flip to 'done' before on_complete ran, so a
    poller could see 'done' with an empty result (model not yet in catalog)."""
    data = b"r" * 5000
    monkeypatch.setattr(md, "open_url", FakeServer({"m.gguf": data}).open_url)
    def slow_register(job):
        time.sleep(0.3)
        return {"registered": True, "model_id": 1}
    job = mgr.start("o/r", cand([("m.gguf", data)]), tmp_path, on_complete=slow_register)
    seen_done_without_result = False
    t0 = time.time()
    while time.time() - t0 < 5:
        if job.status == "done":
            seen_done_without_result = not job.result
            break
        time.sleep(0.005)
    assert job.status == "done" and not seen_done_without_result
