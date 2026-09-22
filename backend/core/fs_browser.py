"""
backend/core/fs_browser.py

Server-side directory listing for the in-app file/folder picker.

Why this exists instead of a plain <input type="file">: a browser only
hands the page a file's *contents* (and a fake path), never the real
absolute path -- but this app needs the real path, because the runtime
launches `llama-server` against it and the model catalog stores it. The
backend runs on the same machine as the user, so it can simply list the
disk itself and let the frontend render a picker on top.

Security note: this can list any directory the backend process can read.
That's the point of a local file picker, and it's only reachable with the
local access token, on a server bound to 127.0.0.1 -- same trust
boundary as everything else in this app. It only ever *lists* (names,
sizes, dates); it never reads or returns file contents.
"""

from __future__ import annotations

import os
import platform
import string
from pathlib import Path
from typing import Optional

MAX_ENTRIES = 3000
IS_WINDOWS = platform.system() == "Windows"

WINDOWS_EXECUTABLE_EXTENSIONS = {".exe", ".bat", ".cmd", ".ps1", ".com"}


class FsBrowseError(Exception):
    """Raised with a user-facing (Persian) message; the API layer maps it to a 400."""


def _clean(path: str) -> str:
    # Same forgiving input handling as core/models.py: Windows' "Copy as
    # path" wraps the path in double quotes.
    return path.strip().strip('"').strip("'")


def _is_hidden(name: str, st_file_attributes: Optional[int]) -> bool:
    if name.startswith("."):
        return True
    if IS_WINDOWS and st_file_attributes is not None:
        return bool(st_file_attributes & 0x2)  # FILE_ATTRIBUTE_HIDDEN
    return False


def list_roots() -> list[dict]:
    """Quick-jump locations: home + its common subfolders, and every drive
    on Windows / the filesystem root elsewhere."""
    roots: list[dict] = []
    home = Path.home()
    roots.append({"name": "Home", "path": str(home)})
    for sub in ("Desktop", "Downloads", "Documents"):
        candidate = home / sub
        if candidate.is_dir():
            roots.append({"name": sub, "path": str(candidate)})

    if IS_WINDOWS:
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                roots.append({"name": drive, "path": drive})
    else:
        roots.append({"name": "/", "path": "/"})
    return roots


def _parent_of(p: Path) -> Optional[str]:
    parent = p.parent
    if parent != p:
        return str(parent)
    # `p` is a filesystem root. On Windows, "up" from a drive root means
    # the drive list (represented by the empty string); on POSIX there's
    # nothing above "/".
    return "" if IS_WINDOWS else None


def list_directory(
    path: Optional[str] = None,
    *,
    kind: str = "any",
    extensions: Optional[list[str]] = None,
    executable_only: bool = False,
    show_hidden: bool = False,
) -> dict:
    """Lists one directory level.

    kind:
      "dir"  -- folders only (folder picker)
      "file" -- folders (so you can navigate) + files, optionally filtered
      "any"  -- same as "file" with no extension filter implied
    extensions: e.g. [".gguf"]; case-insensitive; only applied to files.
    executable_only: only show files that can be launched (exec bit on
      POSIX; .exe/.bat/... on Windows). Folders are always shown so you
      can navigate into them.

    `path` of "" (Windows only) or None returns the drives / home view.
    """
    if kind not in ("dir", "file", "any"):
        raise FsBrowseError(f"kind نامعتبر: {kind}")

    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in (extensions or [])}
    roots = list_roots()

    if path is None:
        path = str(Path.home())

    if path == "":
        if not IS_WINDOWS:
            path = "/"
        else:
            drives = [r for r in roots if len(r["path"]) == 3 and r["path"].endswith(":\\")]
            return {
                "path": "",
                "parent": None,
                "entries": [
                    {"name": d["name"], "path": d["path"], "is_dir": True, "size": None} for d in drives
                ],
                "roots": roots,
                "truncated": False,
            }

    cleaned = _clean(path)
    p = Path(cleaned).expanduser()
    try:
        p = p.resolve()
    except OSError as exc:
        raise FsBrowseError(f"مسیر معتبر نیست: {cleaned}") from exc

    # Being pointed at a file (e.g. pasted full path) means "show its folder".
    if p.is_file():
        p = p.parent
    if not p.is_dir():
        raise FsBrowseError(f"«{cleaned}» یه پوشه‌ی معتبر نیست.")

    entries: list[dict] = []
    truncated = False
    try:
        with os.scandir(p) as it:
            for entry in it:
                try:
                    st = entry.stat()  # follows symlinks; broken links raise OSError
                    is_dir = entry.is_dir()
                except OSError:
                    continue

                attrs = getattr(st, "st_file_attributes", None)
                if not show_hidden and _is_hidden(entry.name, attrs):
                    continue

                if is_dir:
                    entries.append({"name": entry.name, "path": entry.path, "is_dir": True, "size": None})
                else:
                    if kind == "dir":
                        continue
                    suffix = Path(entry.name).suffix.lower()
                    if exts and suffix not in exts:
                        continue
                    if executable_only:
                        if IS_WINDOWS:
                            if suffix not in WINDOWS_EXECUTABLE_EXTENSIONS:
                                continue
                        elif not os.access(entry.path, os.X_OK):
                            continue
                    entries.append({"name": entry.name, "path": entry.path, "is_dir": False, "size": st.st_size})

                if len(entries) >= MAX_ENTRIES:
                    truncated = True
                    break
    except PermissionError as exc:
        raise FsBrowseError(f"اجازه‌ی خوندن «{p}» رو نداریم.") from exc
    except OSError as exc:
        raise FsBrowseError(f"خوندن «{p}» ممکن نشد: {exc.strerror or exc}") from exc

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return {
        "path": str(p),
        "parent": _parent_of(p),
        "entries": entries,
        "roots": roots,
        "truncated": truncated,
    }
