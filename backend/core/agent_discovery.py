"""
backend/core/agent_discovery.py

The "online" half of Settings -> Browse for agents: search GitHub for an
agent project, read its README's install instructions, and -- only after
the user has looked at the exact command and confirmed -- run it.

Why this is stricter than the model download side: downloading a GGUF
file is inert data; installing an agent means running code and commands
somebody else wrote. So:

  1. Search results carry *signals* (stars, owner, license, last push,
     archived) -- never a "verified" badge. GitHub can't tell us a repo
     is trustworthy, and this module doesn't pretend to.
  2. Install commands are pulled from the README and shown as-is. Two
     shapes are runnable from the UI, both parsed strictly:
       - a package-manager install (pip/pipx/uv/npm -g/cargo/brew, with a
         fixed set of flags and plain package names / GitHub URLs);
       - a script installer: exactly `curl|wget <one https URL> | bash|sh`
         (what many agent projects, Hermes included, use). It's run as two
         processes joined by a pipe -- the URL is a single argv element,
         so it can't inject anything -- and it's flagged "script" with the
         host it downloads from, so the confirmation dialog can say
         plainly that it downloads and runs code.
     Everything else -- `sudo`, chained commands, redirects, custom package
     indexes, `-k`/`-o` curl flags -- is still shown but marked "run it
     yourself".
  3. The server re-parses the command on the confirm request. The browser
     is never trusted to say "this one is safe".
  4. No shell is ever involved (`shell=False`), so a command string can't
     smuggle in a second command.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

from .net_util import DiscoveryError, get_json, get_text

GITHUB_API = "https://api.github.com"
GITHUB_ALLOWED_HOSTS = ("github.com", "githubusercontent.com")
SEARCH_PER_PAGE = 10
MAX_LOG_LINES = 400
INSTALL_TIMEOUT_SECONDS = 900

REPO_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


# ---------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------

def search_agents(query: str) -> list[dict]:
    q = query.strip()
    if not q:
        raise DiscoveryError("اسم agent (حتی تقریبی) رو بنویس.", "bad_input")

    url = (
        f"{GITHUB_API}/search/repositories?q={quote(q + ' in:name,description,topics fork:false')}"
        f"&per_page={SEARCH_PER_PAGE}"
    )
    data = get_json(url, allowed_hosts=GITHUB_ALLOWED_HOSTS, headers={"Accept": "application/vnd.github+json"})
    results = []
    for item in data.get("items", []):
        full_name = item.get("full_name")
        if not isinstance(full_name, str) or not REPO_FULL_NAME_RE.match(full_name):
            continue
        owner = item.get("owner") or {}
        license_info = item.get("license") or {}
        results.append(
            {
                "full_name": full_name,
                "description": item.get("description"),
                "stars": item.get("stargazers_count") or 0,
                "url": item.get("html_url"),
                "language": item.get("language"),
                "license": license_info.get("spdx_id"),
                "last_push": item.get("pushed_at"),
                "archived": bool(item.get("archived")),
                "owner_type": owner.get("type"),  # "Organization" vs "User"
                "topics": item.get("topics") or [],
            }
        )
    return results


# ---------------------------------------------------------------------
# Install-command extraction + validation
# ---------------------------------------------------------------------

_SAFE_FLAGS = {"-U", "--upgrade", "--user", "-g", "--global", "--locked", "--force", "--no-cache-dir", "--pre"}
_PACKAGE_SPEC_RE = re.compile(
    r"^(@[A-Za-z0-9][A-Za-z0-9._\-]*/)?[A-Za-z0-9][A-Za-z0-9._\-]*"  # name (npm scopes allowed)
    r"(\[[A-Za-z0-9._,\-]+\])?"  # pip extras: pkg[all]
    r"(==[A-Za-z0-9.*+\-]+|@[A-Za-z0-9.\-]+)?$"  # ==1.2.3 (pip) or @latest / @1.2.3 (npm)
)
_GITHUB_PIP_URL_RE = re.compile(r"^git\+https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+(\.git)?(@[A-Za-z0-9._/\-]+)?$")
_SHELL_META = set("|&;<>`$(){}\\\n*?!#")


def validate_install_command(command: str) -> tuple[bool, Optional[str], Optional[list[str]]]:
    """Returns (runnable, reason_if_not, argv_if_runnable). Pure function:
    used both when listing candidates and again when the user confirms."""
    cmd = command.strip()
    if not cmd:
        return False, "خالیه.", None
    if len(cmd) > 300:
        return False, "خیلی طولانیه.", None
    if any(ch in _SHELL_META for ch in cmd):
        return False, "شامل pipe / زنجیره‌ی دستور / متغیر شل هست — خودت توی ترمینال اجرا کن.", None

    try:
        argv = shlex.split(cmd, posix=(os.name != "nt"))
    except ValueError:
        return False, "قابل تجزیه نبود.", None
    argv = [a.strip("\"'") for a in argv]  # non-posix split keeps the quote characters
    if not argv:
        return False, "خالیه.", None

    first = argv[0].lower()
    rest = argv[1:]

    # Normalize `python -m pip ...` / `py -m pip ...` down to a pip-style check.
    manager = first
    if first in ("python", "python3", "py") and rest[:2] == ["-m", "pip"]:
        manager, rest = "pip", rest[2:]

    if manager in ("pip", "pip3"):
        if not rest or rest[0] != "install":
            return False, "فقط `pip install` مجازه.", None
        args = rest[1:]
    elif manager == "pipx":
        if not rest or rest[0] != "install":
            return False, "فقط `pipx install` مجازه.", None
        args = rest[1:]
    elif manager == "uv":
        if rest[:2] not in (["tool", "install"], ["pip", "install"]):
            return False, "فقط `uv tool install` / `uv pip install` مجازه.", None
        args = rest[2:]
    elif manager == "npm":
        if not rest or rest[0] not in ("install", "i"):
            return False, "فقط `npm install -g` مجازه.", None
        args = rest[1:]
        if not ({"-g", "--global"} & set(args)):
            return False, "npm فقط با -g (نصب سراسری) مجازه.", None
    elif manager == "cargo":
        if not rest or rest[0] != "install":
            return False, "فقط `cargo install` مجازه.", None
        args = rest[1:]
    elif manager == "brew":
        if not rest or rest[0] != "install":
            return False, "فقط `brew install` مجازه.", None
        args = rest[1:]
    else:
        return False, f"`{argv[0]}` توی لیست دستورهای قابل اجرا از داخل برنامه نیست.", None

    packages = []
    for a in args:
        if a.startswith("-"):
            if a not in _SAFE_FLAGS:
                return False, f"فلگ `{a}` مجاز نیست.", None
        else:
            packages.append(a)
    if not packages:
        return False, "اسم پکیجی توی دستور نیست.", None
    for pkg in packages:
        if not (_PACKAGE_SPEC_RE.match(pkg) or _GITHUB_PIP_URL_RE.match(pkg)):
            return False, f"«{pkg}» شبیه یه اسم پکیج ساده یا آدرس GitHub نیست.", None

    # `python -m pip install ...` was reduced to a pip check above; put the
    # launcher back so the command that runs is exactly what was validated.
    if manager == "pip" and first in ("python", "python3", "py"):
        return True, None, [argv[0], "-m", "pip", "install"] + args
    return True, None, argv


# ---------------------------------------------------------------------
# Script installers: `curl|wget <https url> | bash|sh`
# ---------------------------------------------------------------------

@dataclass
class InstallPlan:
    """What actually gets run for a validated command.

    kind "package": `steps` is one argv.
    kind "script":  `steps` is [fetch_argv, shell_argv]; the fetcher's
                    stdout is piped into the shell's stdin (no shell
                    involved -- the pipe is set up by the runner itself).
    """

    kind: str
    steps: list[list[str]]
    script_url: Optional[str] = None
    script_host: Optional[str] = None


# `|` is handled separately (exactly one is required); these are never allowed.
_SCRIPT_FORBIDDEN = set("`$(){}\\;&<>*?!#\n")
_CURL_SHORT_FLAG_CHARS = set("fsSL")  # --fail --silent --show-error --location
_CURL_LONG_FLAGS = {"--fail", "--silent", "--show-error", "--location", "--tlsv1.2", "--tlsv1.3"}
_WGET_FLAGS = {"-q", "--quiet", "-qO-", "-O-"}
_SCRIPT_URL_RE = re.compile(
    r"^https://(?P<host>[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)(?::\d{1,5})?"
    r"(?:/[A-Za-z0-9._~%/+=-]*)?(?:\?[A-Za-z0-9._~%/+=-]*)?$"
)
_SHELL_ARG_RE = re.compile(r"^[A-Za-z0-9._=:/-]+$")
_SHELLS = {"bash", "sh", "/bin/bash", "/bin/sh"}


def _split_tokens(part: str) -> list[str]:
    tokens = shlex.split(part, posix=(os.name != "nt"))
    return [t.strip("\"'") for t in tokens]  # non-posix split keeps the quote characters


def parse_script_installer(command: str) -> tuple[Optional[InstallPlan], Optional[str]]:
    """Strict parser for `curl -fsSL https://host/install.sh | bash`-style
    commands. Returns (plan, None) or (None, reason)."""
    cmd = command.strip()
    if len(cmd) > 400:
        return None, "خیلی طولانیه."
    if cmd.count("|") != 1:
        return None, "فقط یه pipe مجازه (دانلود | اجرا)."
    if any(ch in _SCRIPT_FORBIDDEN for ch in cmd):
        return None, "شامل کاراکتر یا ساختار شل غیرمجاز (زنجیره‌ی دستور، متغیر، redirect) هست."

    left_raw, right_raw = cmd.split("|")
    try:
        left, right = _split_tokens(left_raw), _split_tokens(right_raw)
    except ValueError:
        return None, "قابل تجزیه نبود."
    if not left or not right:
        return None, "قابل تجزیه نبود."

    # ---- left: the downloader ----
    fetcher = os.path.basename(left[0]).lower().removesuffix(".exe")
    if fetcher not in ("curl", "wget"):
        return None, f"سمت چپ pipe فقط curl یا wget می‌تونه باشه، نه `{left[0]}`."
    fetch_args: list[str] = []
    url: Optional[str] = None
    wget_to_stdout = False
    i = 1
    while i < len(left):
        t = left[i]
        if fetcher == "curl":
            if t == "--proto":
                if i + 1 >= len(left) or left[i + 1] != "=https":
                    return None, "`--proto` فقط با `=https` مجازه."
                fetch_args += [t, left[i + 1]]
                i += 2
                continue
            if t in _CURL_LONG_FLAGS:
                fetch_args.append(t)
            elif t.startswith("-") and not t.startswith("--") and len(t) > 1 and set(t[1:]) <= _CURL_SHORT_FLAG_CHARS:
                fetch_args.append(t)
            elif t.startswith("-"):
                return None, f"فلگ `{t}` برای curl مجاز نیست."
            else:
                if url is not None:
                    return None, "بیشتر از یه آدرس توی دستور هست."
                url = t
        else:  # wget
            if t == "-O" and i + 1 < len(left) and left[i + 1] == "-":
                fetch_args += ["-O", "-"]
                wget_to_stdout = True
                i += 2
                continue
            if t in _WGET_FLAGS:
                fetch_args.append(t)
                wget_to_stdout = wget_to_stdout or t in ("-qO-", "-O-")
            elif t.startswith("-"):
                return None, f"فلگ `{t}` برای wget مجاز نیست."
            else:
                if url is not None:
                    return None, "بیشتر از یه آدرس توی دستور هست."
                url = t
        i += 1

    if url is None:
        return None, "آدرسی توی دستور نیست."
    if fetcher == "wget" and not wget_to_stdout:
        return None, "wget باید خروجی رو روی stdout بنویسه (‎-O-‎)."
    m = _SCRIPT_URL_RE.match(url)
    if not m:
        return None, "آدرس باید https ساده باشه (بدون user@ و کاراکتر عجیب)."

    # ---- right: the shell ----
    shell = right[0]
    if shell not in _SHELLS:
        return None, f"سمت راست pipe فقط bash یا sh می‌تونه باشه (بدون sudo)، نه `{shell}`."
    shell_args: list[str] = []
    after_dashdash = False
    for t in right[1:]:
        if t == "--":
            after_dashdash = True
        elif not after_dashdash and t.startswith("-") and t not in ("-s", "-"):
            return None, f"فلگ `{t}` برای shell مجاز نیست."
        elif t not in ("-s", "-", "--") and not _SHELL_ARG_RE.match(t):
            return None, f"آرگومان `{t}` مجاز نیست."
        shell_args.append(t)
    if len(shell_args) > 10:
        return None, "آرگومان‌های اسکریپت زیادن."

    fetch_argv = [os.path.basename(left[0]).removesuffix(".exe") if os.name == "nt" else left[0]] + fetch_args + [url]
    return (
        InstallPlan("script", [fetch_argv, [shell] + shell_args], script_url=url, script_host=m.group("host").lower()),
        None,
    )


def plan_install_command(command: str) -> tuple[Optional[InstallPlan], Optional[str]]:
    """The one entry point both the candidate listing and the confirm/run
    path use, so what's shown as runnable is exactly what will run."""
    if "|" in command:
        return parse_script_installer(command)
    ok, reason, argv = validate_install_command(command)
    if ok and argv:
        return InstallPlan("package", [argv]), None
    return None, reason


_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def extract_install_commands(readme: str, limit: int = 12) -> list[dict]:
    """Pulls candidate install lines out of a README's fenced code blocks
    (with or without a leading `$ ` prompt), keeping order and dropping
    duplicates. Each gets a runnable/not verdict. Instructions written
    only in prose, or inside indented (non-fenced) blocks, are not picked
    up -- the UI always links the README itself for that case."""
    lines: list[str] = []
    for block in _FENCE_RE.findall(readme):
        lines.extend(block.splitlines())

    seen: set[str] = set()
    out: list[dict] = []
    trigger = re.compile(
        r"^(?:\$\s*|>\s*)?((?:python3?\s+-m\s+pip|py\s+-m\s+pip|pip3?|pipx|uv|npm|cargo|brew|curl|wget|git\s+clone|sudo|irm|iwr)\b.*)$",
        re.IGNORECASE,
    )
    for raw in lines:
        m = trigger.match(raw.strip())
        if not m:
            continue
        cmd = m.group(1).strip()
        low = cmd.lower()
        # Only lines that are about installing something.
        if not any(k in low for k in ("install", "curl", "wget", "irm", "iwr", "git clone")):
            continue
        if cmd in seen:
            continue
        seen.add(cmd)
        plan, reason = plan_install_command(cmd)
        out.append(
            {
                "command": cmd,
                "runnable": plan is not None,
                "reason": reason,
                "kind": plan.kind if plan else None,
                "host": plan.script_host if plan else None,
                "notes": [],
            }
        )
        if len(out) >= limit:
            break
    # Runnable first, and package-manager installs before script installers
    # (the safer option is the first thing shown). Stable within each group.
    out.sort(key=lambda c: (not c["runnable"], c["kind"] == "script"))
    return out


def _is_windows_wsl_launcher(path: str) -> bool:
    """`bash.exe` in System32 is the WSL launcher: it would run the script
    inside a Linux distro, not on Windows."""
    return os.name == "nt" and "\\windows\\system32\\" in path.lower()


def annotate_environment(entry: dict) -> dict:
    """Adds machine-specific facts to a script-installer entry (are curl
    and bash even here? is bash really WSL?). Kept out of the pure parser
    so parsing stays deterministic and testable."""
    if not entry.get("runnable") or entry.get("kind") != "script":
        return entry
    plan, _ = plan_install_command(entry["command"])
    if plan is None:
        return entry
    for step in plan.steps:
        tool = step[0]
        resolved = shutil.which(tool)
        if resolved is None:
            hint = " (روی ویندوز: Git for Windows یا WSL لازمه)" if tool in ("bash", "sh") and os.name == "nt" else ""
            entry["runnable"] = False
            entry["reason"] = f"`{tool}` روی این سیستم نصب نیست{hint}."
            return entry
        if tool in ("bash", "sh") and _is_windows_wsl_launcher(resolved):
            entry["notes"].append(
                "bash اینجا لانچر WSL هست: اسکریپت داخل WSL اجرا می‌شه، نه روی خود ویندوز — برنامه‌ی نصب‌شده رو ویندوز نمی‌بینه."
            )
    return entry


def get_install_candidates(full_name: str) -> dict:
    if not REPO_FULL_NAME_RE.match(full_name):
        raise DiscoveryError("اسم repo معتبر نیست (باید به شکل owner/name باشه).", "bad_input")
    readme = get_text(
        f"{GITHUB_API}/repos/{quote(full_name, safe='/')}/readme",
        allowed_hosts=GITHUB_ALLOWED_HOSTS,
        headers={"Accept": "application/vnd.github.raw+json"},
    )
    return {
        "full_name": full_name,
        "repo_url": f"https://github.com/{full_name}",
        "commands": [annotate_environment(c) for c in extract_install_commands(readme)],
    }


# ---------------------------------------------------------------------
# Install runner
# ---------------------------------------------------------------------

@dataclass
class InstallJob:
    id: str
    repo: str
    command: str
    steps: list[list[str]]
    kind: str = "package"
    status: str = "running"  # running | done | error
    log: list[str] = field(default_factory=list)
    returncode: Optional[int] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "repo": self.repo,
            "command": self.command,
            "kind": self.kind,
            "status": self.status,
            "log": self.log[-MAX_LOG_LINES:],
            "returncode": self.returncode,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class InstallManager:
    def __init__(self) -> None:
        self._jobs: dict[str, InstallJob] = {}
        self._lock = threading.Lock()

    def start(self, repo: str, command: str) -> InstallJob:
        """Raises DiscoveryError('bad_input') if the command isn't one of
        the two accepted shapes -- checked here again, server-side,
        regardless of what the UI showed."""
        plan, reason = plan_install_command(command)
        if plan is None:
            raise DiscoveryError(f"این دستور از داخل برنامه قابل اجرا نیست: {reason}", "bad_input")

        steps: list[list[str]] = []
        for step in plan.steps:
            resolved = shutil.which(step[0])
            if resolved is None:
                hint = " (روی ویندوز: Git for Windows یا WSL لازمه)" if step[0] in ("bash", "sh") and os.name == "nt" else ""
                raise DiscoveryError(f"`{step[0]}` روی این سیستم نصب نیست (روی PATH پیدا نشد){hint}.", "bad_input")
            steps.append([resolved] + step[1:])

        with self._lock:
            for j in self._jobs.values():
                if j.status == "running" and j.command == command:
                    return j
            job = InstallJob(id=uuid.uuid4().hex[:12], repo=repo, command=command, steps=steps, kind=plan.kind)
            self._jobs[job.id] = job

        threading.Thread(target=self._run, args=(job,), daemon=True, name=f"install-{job.id}").start()
        return job

    def get(self, job_id: str) -> Optional[InstallJob]:
        return self._jobs.get(job_id)

    # -- runners --

    def _run(self, job: InstallJob) -> None:
        try:
            if len(job.steps) == 1:
                self._run_single(job)
            else:
                self._run_pipeline(job)
        except OSError as exc:
            job.status, job.error = "error", f"اجرای دستور ممکن نشد: {exc.strerror or exc}"
        finally:
            job.finished_at = time.time()

    @staticmethod
    def _append(job: InstallJob, line: str) -> None:
        job.log.append(line)
        if len(job.log) > MAX_LOG_LINES * 2:
            del job.log[:MAX_LOG_LINES]

    def _run_single(self, job: InstallJob) -> None:
        proc = subprocess.Popen(
            job.steps[0],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
            shell=False,
        )
        timer = threading.Timer(INSTALL_TIMEOUT_SECONDS, proc.kill)
        timer.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self._append(job, line.rstrip("\n"))
            proc.wait()
        finally:
            timer.cancel()
        job.returncode = proc.returncode
        job.status = "done" if proc.returncode == 0 else "error"
        if proc.returncode != 0:
            job.error = f"نصب با کد خروج {proc.returncode} تموم شد — لاگ رو ببین."

    def _run_pipeline(self, job: InstallJob) -> None:
        """fetcher | shell, wired up here with two processes and no shell.
        Both exit codes matter: `curl -f` failing (404, TLS error) leaves
        the shell reading an empty script, which "succeeds" -- so a
        failed download must be reported as a failed install."""
        fetch_argv, shell_argv = job.steps
        fetch = subprocess.Popen(
            fetch_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, shell=False
        )
        try:
            shell = subprocess.Popen(
                shell_argv,
                stdin=fetch.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                shell=False,
            )
        except OSError:
            fetch.kill()
            raise
        assert fetch.stdout is not None and fetch.stderr is not None and shell.stdout is not None
        fetch.stdout.close()  # the shell owns the read end now; lets the fetcher see a closed pipe if the shell exits early

        def drain_fetch_stderr() -> None:
            for raw in fetch.stderr:  # type: ignore[union-attr]
                text = raw.decode("utf-8", errors="replace").rstrip()
                if text:
                    self._append(job, f"[{os.path.basename(fetch_argv[0])}] {text}")

        drain = threading.Thread(target=drain_fetch_stderr, daemon=True)
        drain.start()

        def kill_both() -> None:
            for p in (fetch, shell):
                try:
                    p.kill()
                except OSError:
                    pass

        timer = threading.Timer(INSTALL_TIMEOUT_SECONDS, kill_both)
        timer.start()
        try:
            for line in shell.stdout:
                self._append(job, line.rstrip("\n"))
            shell.wait()
            fetch.wait()
            drain.join(timeout=2)
        finally:
            timer.cancel()

        if fetch.returncode != 0:
            job.returncode = fetch.returncode
            job.status = "error"
            job.error = f"دانلود اسکریپت شکست خورد (کد {fetch.returncode}) — چیزی اجرا نشده یا ناقص اجرا شده. لاگ رو ببین."
        elif shell.returncode != 0:
            job.returncode = shell.returncode
            job.status = "error"
            job.error = f"اسکریپت با کد خروج {shell.returncode} تموم شد — لاگ رو ببین."
        else:
            job.returncode = 0
            job.status = "done"


install_manager = InstallManager()
