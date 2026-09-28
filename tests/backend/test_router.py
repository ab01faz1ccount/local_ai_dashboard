import hashlib
import os
import stat
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.storage import db as storage_db
from backend.api import http, discovery
from backend.core import connectivity, model_discovery as md
from backend.core.agents import paths as agent_paths
from backend.core.security import get_or_create_access_token

# The real token, not a hardcoded fake one: http.py computes its own
# _ACCESS_TOKEN at import time via this exact function, reading/writing
# the same cwd-relative local_config.json conftest.py already isolated
# us into -- so this returns the identical value require_token checks
# against, the same way a real client would fetch it once and reuse it.
AUTH = {"Authorization": f"Bearer {get_or_create_access_token()}"}


@pytest.fixture(scope="module", autouse=True)
def _init():
    storage_db.init_db()


@pytest.fixture
def net(monkeypatch):
    """Flip `net["online"]` to simulate losing the internet connection."""
    state = {"online": True}
    monkeypatch.setattr(connectivity, "check_internet", lambda force=False: state["online"])
    return state


@pytest.fixture
def client(net):
    app = FastAPI()
    app.include_router(http.router)
    app.include_router(discovery.router)
    return TestClient(app)


def get(c, path, **kw):
    return c.get(f"/api/v1/discover{path}", headers=AUTH, **kw)


def post(c, path, body, **kw):
    return c.post(f"/api/v1/discover{path}", headers=AUTH, json=body, **kw)


def test_every_route_requires_token(client):
    routes = [r for r in discovery.router.routes]
    assert routes
    for r in routes:
        path = r.path.replace("{job_id}", "x")
        for method in r.methods - {"HEAD", "OPTIONS"}:
            resp = client.request(method, path)
            assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


# ---------------- offline ----------------

def test_fs_list_endpoint(client, tmp_path):
    (tmp_path / "m.gguf").write_bytes(b"x")
    (tmp_path / "other.txt").write_text("y")
    r = get(client, "/fs/list", params={"path": str(tmp_path), "kind": "file", "ext": ".gguf"})
    assert r.status_code == 200
    assert [e["name"] for e in r.json()["entries"]] == ["m.gguf"]
    assert get(client, "/fs/list", params={"path": str(tmp_path / "nope")}).status_code == 400
    assert get(client, "/fs/list", params={"kind": "bogus"}).status_code == 400


def test_register_local_file_then_folder(client, tmp_path):
    f = tmp_path / "Tiny-Q4_K_M.gguf"
    f.write_bytes(b"g" * 123)
    r = post(client, "/models/register-local", {"path": f'"{f}"'})
    assert r.status_code == 200, r.text
    m = r.json()[0]
    assert m["name"] == "Tiny-Q4_K_M" and m["file_size_bytes"] == 123 and m["file_path"] == str(f.resolve())
    # same file again -> updated in place, not duplicated
    f.write_bytes(b"g" * 456)
    r2 = post(client, "/models/register-local", {"path": str(f)})
    assert r2.json()[0]["id"] == m["id"] and r2.json()[0]["file_size_bytes"] == 456
    # folder -> scan
    d = tmp_path / "dir"; d.mkdir()
    (d / "a.gguf").write_bytes(b"1"); (d / "b.gguf").write_bytes(b"2")
    r3 = post(client, "/models/register-local", {"path": str(d)})
    assert r3.status_code == 200 and {x["name"] for x in r3.json()} >= {"a", "b", "Tiny-Q4_K_M"}
    # errors
    txt = tmp_path / "x.txt"; txt.write_text("no")
    assert post(client, "/models/register-local", {"path": str(txt)}).status_code == 400
    assert post(client, "/models/register-local", {"path": str(tmp_path / "ghost.gguf")}).status_code == 400
    onb = client.get("/api/v1/onboarding/state", headers=AUTH).json()
    assert onb["first_model_added"] is True


def test_register_split_model_requires_first_shard_and_all_parts(client, tmp_path):
    for i in (1, 2, 3):
        (tmp_path / f"big-0000{i}-of-00003.gguf").write_bytes(b"s" * 10)
    ok = post(client, "/models/register-local", {"path": str(tmp_path / "big-00001-of-00003.gguf")})
    assert ok.status_code == 200 and ok.json()[0]["name"] == "big" and ok.json()[0]["file_size_bytes"] == 30
    mid = post(client, "/models/register-local", {"path": str(tmp_path / "big-00002-of-00003.gguf")})
    assert mid.status_code == 400
    (tmp_path / "big-00003-of-00003.gguf").unlink()
    (tmp_path / "part-00001-of-00002.gguf").write_bytes(b"1")
    assert post(client, "/models/register-local", {"path": str(tmp_path / "part-00001-of-00002.gguf")}).status_code == 400


@pytest.mark.skipif(os.name == "nt", reason="uses a shell-script stand-in for the CLI")
def test_register_local_agent(client, tmp_path):
    exe = tmp_path / "hermes"
    exe.write_text("#!/bin/sh\necho 'hermes 9.9.9'\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    try:
        r = post(client, "/agents/register-local", {"path": str(exe)})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["created"] is True and j["agent"]["agent_backend"] == "hermes" and j["detected_version"] == "hermes 9.9.9"
        assert agent_paths.get_path("hermes") == str(exe.resolve())
        # the adapter now resolves to the picked path everywhere
        from backend.core import agents
        adapter = agents.get_adapter("hermes")
        assert adapter.build_launch_command() == [str(exe.resolve())]
        assert adapter.build_launch_command("abc") == [str(exe.resolve()), "-r", "abc"]
        # and shows up as detected in the existing endpoint
        backends = client.get("/api/v1/agent-backends", headers=AUTH).json()
        h = next(b for b in backends if b["backend_id"] == "hermes")
        assert h["detected"] is True and h["detected_path"] == str(exe.resolve())
        # second registration reuses the Agent
        r2 = post(client, "/agents/register-local", {"path": str(exe)})
        assert r2.json()["created"] is False and r2.json()["agent"]["id"] == j["agent"]["id"]
    finally:
        agent_paths.clear_path("hermes")


@pytest.mark.skipif(os.name == "nt", reason="posix perms")
def test_register_local_agent_errors(client, tmp_path):
    other = tmp_path / "notanagent"; other.write_text("#!/bin/sh\n"); other.chmod(0o755)
    r = post(client, "/agents/register-local", {"path": str(other)})
    assert r.status_code == 400 and "hermes" in r.text
    noexec = tmp_path / "hermes"; noexec.write_text("x"); noexec.chmod(0o644)
    r = post(client, "/agents/register-local", {"path": str(noexec)})
    assert r.status_code == 400 and agent_paths.get_path("hermes") is None  # nothing persisted on failure
    assert post(client, "/agents/register-local", {"path": str(tmp_path / "zzz")}).status_code == 400


def test_bad_previous_path_is_restored_on_failed_registration(tmp_path):
    # unit-level: a stale/missing stored path must read as "not set", not crash
    agent_paths.set_path("hermes", str(tmp_path / "deleted"))
    assert agent_paths.get_path("hermes") is None
    agent_paths.clear_path("hermes")


# ---------------- models dir ----------------

def test_models_dir_roundtrip(client, tmp_path):
    default = get(client, "/models/dir").json()["path"]
    assert default.endswith("models")
    target = tmp_path / "my" / "models"
    r = client.put("/api/v1/discover/models/dir", headers=AUTH, json={"path": str(target)})
    assert r.status_code == 200 and target.is_dir()
    assert get(client, "/models/dir").json()["path"] == str(target.resolve())
    assert client.put("/api/v1/discover/models/dir", headers=AUTH, json={"path": "  "}).status_code == 400


# ---------------- online (network faked) ----------------

def test_online_routes_refuse_when_offline(client, net):
    net["online"] = False
    for path in ("/models/search?q=llama", "/models/files?repo=a/b", "/agents/search?q=x", "/agents/install-candidates?repo=a/b"):
        r = get(client, path)
        assert r.status_code == 400 and "اینترنت" in r.text, path
    assert post(client, "/models/download", {"repo_id": "a/b", "filename": "x.gguf"}).status_code == 400


def test_search_error_mapping(client, monkeypatch):
    from backend.core.net_util import DiscoveryError
    def boom(q): raise DiscoveryError("x", "rate")
    monkeypatch.setattr(md, "search_models", boom)
    assert get(client, "/models/search?q=llama").status_code == 429
    monkeypatch.setattr(md, "search_models", lambda q: [{"repo_id": "a/b"}])
    assert get(client, "/models/search?q=llama").json() == [{"repo_id": "a/b"}]


def test_download_end_to_end_registers_with_hf_link(client, tmp_path, monkeypatch):
    data = b"GGUF" + os.urandom(200_000)
    sha = hashlib.sha256(data).hexdigest()
    cand = {"filename": "Cool-3B-Instruct-Q5_K_M.gguf",
            "files": [{"path": "Cool-3B-Instruct-Q5_K_M.gguf", "size": len(data), "sha256": sha}]}
    monkeypatch.setattr(md, "get_json", lambda url, **kw: [
        {"type": "file", "path": "Cool-3B-Instruct-Q5_K_M.gguf", "size": len(data), "lfs": {"oid": sha, "size": len(data)}}])

    class Resp:
        status = 200
        def __init__(s): s.pos = 0
        def read(s, n=-1):
            out = data[s.pos:s.pos + min(n, 65536)]; s.pos += len(out); return out
        def __enter__(s): return s
        def __exit__(s, *a): return False
    monkeypatch.setattr(md, "open_url", lambda url, **kw: Resp())

    dest = tmp_path / "dl"
    assert client.put("/api/v1/discover/models/dir", headers=AUTH, json={"path": str(dest)}).status_code == 200

    r = post(client, "/models/download", {"repo_id": "acme/Cool-3B-Instruct-GGUF", "filename": cand["filename"]})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    for _ in range(200):
        j = get(client, f"/downloads/{job_id}").json()
        if j["status"] not in ("queued", "downloading"):
            break
        time.sleep(0.02)
    assert j["status"] == "done", j
    assert j["result"]["registered"] is True
    m = client.get(f"/api/v1/models/{j['result']['model_id']}", headers=AUTH).json()
    assert m["hf_repo_id"] == "acme/Cool-3B-Instruct-GGUF" and m["hf_filename"] == cand["filename"]
    assert m["quantization"] == "Q5_K_M" and m["param_count"] == "3B"
    assert m["file_path"] == str((dest / "acme" / "Cool-3B-Instruct-GGUF" / cand["filename"]).resolve())
    assert any(x["id"] == job_id for x in get(client, "/downloads").json())


def test_download_unknown_file_404_and_cancel_unknown(client, monkeypatch):
    monkeypatch.setattr(md, "get_json", lambda url, **kw: [])
    assert post(client, "/models/download", {"repo_id": "a/b", "filename": "nope.gguf"}).status_code == 404
    assert client.post("/api/v1/discover/downloads/zzz/cancel", headers=AUTH).status_code == 404
    assert get(client, "/downloads/zzz").status_code == 404


def test_agent_install_requires_confirmation_and_allowlist(client, monkeypatch):
    r = post(client, "/agents/install", {"repo": "a/b", "command": "pip install foo"})
    assert r.status_code == 400 and "تایید" in r.text
    # not one of the two accepted shapes (two pipes) -> still rejected even confirmed
    r = post(client, "/agents/install", {"repo": "a/b", "command": "curl https://x.sh | bash | tee log", "confirmed": True})
    assert r.status_code == 400
    assert get(client, "/agents/install/nope").status_code == 404


def test_agent_install_script_installer_end_to_end(client, monkeypatch):
    from backend.core import agent_discovery as ad
    import time as _time

    class ClosableIter:
        def __init__(s, items): s._it = iter(items)
        def __iter__(s): return s._it
        def close(s): pass

    class FakeFetch:
        def __init__(s, argv, **kw):
            s.stdout, s.stderr, s.returncode = ClosableIter([]), ClosableIter([]), 0
        def wait(s): pass
        def kill(s): pass

    class FakeShell:
        def __init__(s, argv, **kw):
            s.stdout, s.returncode = iter(["curl output\n"]), 0
        def wait(s): pass
        def kill(s): pass

    calls = {"n": 0}

    def fake_popen(argv, **kw):
        calls["n"] += 1
        return FakeFetch(argv, **kw) if calls["n"] == 1 else FakeShell(argv, **kw)

    monkeypatch.setattr(ad.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ad.shutil, "which", lambda n: "/usr/bin/" + n)
    r = post(client, "/agents/install", {"repo": "a/b", "command": "curl -fsSL https://x.sh | bash", "confirmed": True})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "script"
    job_id = r.json()["id"]
    for _ in range(100):
        j = get(client, f"/agents/install/{job_id}").json()
        if j["status"] != "running":
            break
        _time.sleep(0.02)
    assert j["status"] == "done" and j["log"] == ["curl output"]

    from backend.core import agent_discovery as ad
    class P:
        def __init__(s, argv, **kw): s.stdout = iter(["ok\n"]); s.returncode = 0
        def wait(s): pass
        def kill(s): pass
    monkeypatch.setattr(ad.subprocess, "Popen", P)
    monkeypatch.setattr(ad.shutil, "which", lambda n: "/usr/bin/" + n)
    r = post(client, "/agents/install", {"repo": "a/b", "command": "pip install foo", "confirmed": True})
    assert r.status_code == 200
    jid = r.json()["id"]
    for _ in range(100):
        j = get(client, f"/agents/install/{jid}").json()
        if j["status"] != "running": break
        time.sleep(0.02)
    assert j["status"] == "done" and j["log"] == ["ok"]


def test_removed_suggestions_endpoint_is_gone(client):
    assert client.get("/api/v1/onboarding/model-suggestions", headers=AUTH).status_code == 404


def test_patch_agent_backend_is_applied_and_validated(client):
    r = client.post("/api/v1/agents", headers=AUTH, json={"name": "A1", "agent_backend": "generic"})
    assert r.status_code == 200, r.text
    agent_id = r.json()["id"]

    # bad value: rejected, and the agent is left unchanged
    bad = client.patch(f"/api/v1/agents/{agent_id}", headers=AUTH, json={"agent_backend": "not-a-real-backend"})
    assert bad.status_code == 400
    assert client.get(f"/api/v1/agents/{agent_id}", headers=AUTH).json()["agent_backend"] == "generic"

    # a real registered backend: applied
    ok = client.patch(f"/api/v1/agents/{agent_id}", headers=AUTH, json={"agent_backend": "hermes"})
    assert ok.status_code == 200 and ok.json()["agent_backend"] == "hermes"
    assert client.get(f"/api/v1/agents/{agent_id}", headers=AUTH).json()["agent_backend"] == "hermes"

    # switching back to "generic" is always allowed, no adapter needed
    back = client.patch(f"/api/v1/agents/{agent_id}", headers=AUTH, json={"agent_backend": "generic"})
    assert back.status_code == 200 and back.json()["agent_backend"] == "generic"

    # omitting the field entirely leaves it untouched (merge semantics, not overwrite)
    untouched = client.patch(f"/api/v1/agents/{agent_id}", headers=AUTH, json={"name": "A1 renamed"})
    assert untouched.status_code == 200 and untouched.json()["agent_backend"] == "generic" and untouched.json()["name"] == "A1 renamed"
