"""
backend/core/git_panel.py

A graphical front-end for git, not a reimplementation of it. Per the
explicit decision behind this feature: every operation here shells out
to a real `git` binary and parses its output -- there is no custom
diff/line-counting engine. If a project's folder isn't a git repo, the
right answer is "run `git init`", not a fallback differ.

Every subprocess call passes `git -C <path> <args...>` as a real argument
list (never `shell=True`, never string-interpolated into a shell
command), so a project name or path containing shell metacharacters
can't turn into command injection.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Optional

GIT_TIMEOUT_SECONDS = 15
LOG_FIELD_SEP = "\x1f"  # unit separator -- won't collide with real commit message text


class GitError(Exception):
    """Raised for a git operation that *should* have worked (repo exists,
    git is installed) but the command itself failed -- e.g. a commit
    with a conflicted index. Distinct from the "not available at all"
    cases, which callers check for up front via `git_context`."""


def is_git_available() -> bool:
    return shutil.which("git") is not None


def _run_git(path: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", path, *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )


def is_git_repo(path: str) -> bool:
    try:
        result = _run_git(path, ["rev-parse", "--is-inside-work-tree"])
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def git_context(local_path: Optional[str]) -> dict:
    """The gate every endpoint checks first. One dict shape the frontend
    can render directly into "here's why the Git tab is disabled" states
    without the caller having to know the individual failure modes."""
    if not local_path:
        return {"available": False, "reason": "no_local_path", "message": "این پروژه هنوز به یک پوشه‌ی محلی وصل نشده."}
    if not is_git_available():
        return {
            "available": False,
            "reason": "git_not_installed",
            "message": "روی این سیستم git پیدا نشد. برای استفاده از این بخش باید git نصب باشد.",
        }
    if not is_git_repo(local_path):
        return {
            "available": False,
            "reason": "not_a_repo",
            "message": f"«{local_path}» هنوز یک مخزن git نیست. داخل آن پوشه `git init` را اجرا کنید.",
        }
    return {"available": True, "reason": None, "message": None}


def _parse_porcelain_status(output: str) -> dict:
    staged, unstaged, untracked = 0, 0, 0
    files = []
    for line in output.splitlines():
        if not line:
            continue
        code, _, filepath = line.partition(" ")
        code = line[:2]
        filepath = line[3:]
        if code == "??":
            untracked += 1
        else:
            if code[0] not in (" ", "?"):
                staged += 1
            if code[1] not in (" ", "?"):
                unstaged += 1
        files.append({"path": filepath, "status_code": code})
    return {
        "staged_count": staged,
        "unstaged_count": unstaged,
        "untracked_count": untracked,
        "files": files,
    }


def get_status(local_path: str) -> dict:
    branch_result = _run_git(local_path, ["branch", "--show-current"])
    branch = branch_result.stdout.strip() or None  # empty => detached HEAD

    porcelain_result = _run_git(local_path, ["status", "--porcelain"])
    status = _parse_porcelain_status(porcelain_result.stdout)

    ahead_behind_result = _run_git(local_path, ["rev-list", "--left-right", "--count", "HEAD...@{u}"])
    ahead, behind = None, None
    if ahead_behind_result.returncode == 0:
        parts = ahead_behind_result.stdout.split()
        if len(parts) == 2:
            ahead, behind = int(parts[0]), int(parts[1])

    return {"branch": branch, "ahead": ahead, "behind": behind, **status}


def _parse_numstat(output: str) -> dict:
    files = []
    insertions_total = 0
    deletions_total = 0
    for line in output.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        ins_str, del_str, filepath = parts
        is_binary = ins_str == "-" or del_str == "-"
        insertions = 0 if is_binary else int(ins_str)
        deletions = 0 if is_binary else int(del_str)
        insertions_total += insertions
        deletions_total += deletions
        files.append({"path": filepath, "insertions": insertions, "deletions": deletions, "binary": is_binary})
    return {
        "files_changed": len(files),
        "insertions": insertions_total,
        "deletions": deletions_total,
        "files": files,
    }


def get_diff_stat(local_path: str, staged: bool = False) -> dict:
    """Working-tree (or staged, with `staged=True`) changes vs HEAD.
    Note this does NOT include untracked files -- that's what
    `get_status`'s `untracked_count` is for -- since `git diff` never
    shows content for files git isn't tracking yet."""
    args = ["diff", "--numstat"]
    if staged:
        args.append("--staged")
    result = _run_git(local_path, args)
    return _parse_numstat(result.stdout)


def get_commit_log(local_path: str, limit: int = 20) -> list[dict]:
    fmt = LOG_FIELD_SEP.join(["%H", "%h", "%an", "%ad", "%s"])
    result = _run_git(local_path, ["log", f"-n{limit}", f"--pretty=format:{fmt}", "--date=iso-strict"])
    if result.returncode != 0 or not result.stdout.strip():
        return []  # empty repo (no commits yet) is normal, not an error
    commits = []
    for line in result.stdout.splitlines():
        parts = line.split(LOG_FIELD_SEP)
        if len(parts) != 5:
            continue
        full_hash, short_hash, author, date, subject = parts
        commits.append({"hash": full_hash, "short_hash": short_hash, "author": author, "date": date, "subject": subject})
    return commits


def commit_all(local_path: str, message: str) -> dict:
    """`git add -A` (stages everything, including untracked files) then
    `git commit`. Returns the new commit's stat, or raises GitError with
    git's own message if there's genuinely nothing to commit or the
    commit otherwise fails (e.g. no user.name/user.email configured)."""
    add_result = _run_git(local_path, ["add", "-A"])
    if add_result.returncode != 0:
        raise GitError(add_result.stderr.strip() or "git add failed")

    commit_result = _run_git(local_path, ["commit", "-m", message])
    if commit_result.returncode != 0:
        combined = (commit_result.stdout + commit_result.stderr).strip()
        if "nothing to commit" in combined.lower():
            raise GitError("چیزی برای commit کردن وجود نداشت — همه‌چیز از قبل commit شده بود.")
        raise GitError(combined or "git commit failed")

    show_result = _run_git(local_path, ["show", "--numstat", "--pretty=format:", "HEAD"])
    stat = _parse_numstat(show_result.stdout)
    log = get_commit_log(local_path, limit=1)
    return {**stat, "commit": log[0] if log else None}
