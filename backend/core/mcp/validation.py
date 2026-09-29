"""
backend/core/mcp/validation.py

The security gate in front of every MCP server config. An MCP server of
transport "stdio" is, by definition, "run this program on the user's
machine" -- so, exactly like the agent-install path in
core/agent_discovery.py, nothing here ever goes through a shell and the
launcher has to pass an explicit allow-list:

  * `command` is EITHER a bare launcher name from ALLOWED_LAUNCHERS
    (npx, uvx, node, python, ...) OR an absolute path to a file that
    exists -- a deliberate "run exactly this binary" choice by the user.
  * Shells / script hosts (sh, bash, cmd, powershell, ...) are refused
    under any spelling, including by absolute path -- they'd turn the
    allow-list into "run any string".
  * Interpreters' inline-code flags (`python -c`, `node -e`, ...) are
    refused, for the same reason.
  * `command` never contains shell metacharacters; `args` is a real list
    (each element one argv entry, never re-split or re-joined).
  * `env` can't override variables that hijack program resolution or
    library loading (PATH, LD_PRELOAD, PYTHONPATH, NODE_OPTIONS, ...).

For "http" / "sse" the equivalent checks are: http(s) scheme only, a real
host, no credentials embedded in the URL, and header names/values that
can't smuggle extra headers (no CR/LF).

Every function raises McpValidationError with a user-facing (Persian)
message and returns the normalized value, so the API layer can persist
exactly what was validated.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
from typing import Any, Optional
from urllib.parse import urlsplit

TRANSPORTS = ("stdio", "http", "sse")

# Bare launcher names accepted for stdio servers. Matches what the MCP
# ecosystem actually ships servers with (npm, Python, Deno/Bun, Docker).
ALLOWED_LAUNCHERS = frozenset(
    {"npx", "uvx", "uv", "node", "python", "python3", "py", "deno", "bun", "bunx", "docker"}
)

# Never launchable, whatever the spelling or path.
_FORBIDDEN_EXECUTABLES = frozenset(
    {
        "sh", "bash", "zsh", "dash", "ash", "ksh", "csh", "tcsh", "fish", "busybox",
        "cmd", "command", "powershell", "pwsh", "wscript", "cscript", "mshta",
        "env", "sudo", "su", "doas", "xargs", "eval", "exec", "nohup", "start",
    }
)

_SHELL_METACHARS = re.compile(r"[;&|`$<>\n\r\x00*?\"']")

# (executable stem -> flags that mean "execute this string as code")
_INLINE_CODE_FLAGS: dict[str, frozenset[str]] = {
    "python": frozenset({"-c"}),
    "python3": frozenset({"-c"}),
    "py": frozenset({"-c"}),
    "node": frozenset({"-e", "--eval", "-p", "--print"}),
    "bun": frozenset({"-e", "--eval", "-p", "--print"}),
    "deno": frozenset({"eval"}),
}

_FORBIDDEN_ENV_KEYS = frozenset(
    {
        "PATH", "PATHEXT", "COMSPEC", "SHELL",
        "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH",
        "PYTHONSTARTUP", "PYTHONPATH", "PYTHONHOME", "PYTHONINSPECT",
        "NODE_OPTIONS", "NODE_PATH",
        "BASH_ENV", "ENV", "IFS",
    }
)
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")  # RFC 7230 token

MAX_NAME_LEN = 64
MAX_ARGS = 64
MAX_ARG_LEN = 4096
MAX_ENV_VARS = 64
MAX_VALUE_LEN = 8192
MAX_URL_LEN = 2048


class McpValidationError(ValueError):
    """A config the app refuses to store or run. The message is meant to
    be shown to the user as-is."""


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------

def _stem(command: str) -> str:
    """Lower-cased executable name without directory or extension,
    treating BOTH \\ and / as separators regardless of the host OS (a
    Windows path must be judged the same way when the tests run on
    Linux)."""
    base = ntpath.basename(posixpath.basename(command.replace("\\", "/")))
    base = base.lower()
    for ext in (".exe", ".cmd", ".bat", ".com", ".ps1", ".sh"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    return base


def _is_absolute(command: str) -> bool:
    return posixpath.isabs(command) or ntpath.isabs(command)


# ---------------------------------------------------------------------
# public validators
# ---------------------------------------------------------------------

def validate_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise McpValidationError("اسم سرور نباید خالی باشه.")
    name = name.strip()
    if len(name) > MAX_NAME_LEN:
        raise McpValidationError(f"اسم سرور حداکثر {MAX_NAME_LEN} کاراکتره.")
    if any(ord(c) < 32 for c in name):
        raise McpValidationError("اسم سرور کاراکتر کنترلی نباید داشته باشه.")
    return name


def validate_transport(transport: Any) -> str:
    if transport not in TRANSPORTS:
        raise McpValidationError(f"transport باید یکی از {', '.join(TRANSPORTS)} باشه.")
    return transport


def validate_command(command: Any, *, must_exist: bool = True) -> str:
    """Returns the normalized command. `must_exist` only affects the
    absolute-path branch (bare launchers are resolved by the OS at
    connect time, and connecting to a missing one fails there with a
    clear error)."""
    if not isinstance(command, str) or not command.strip():
        raise McpValidationError("برای transport از نوع stdio، command لازمه.")
    command = command.strip()
    if _SHELL_METACHARS.search(command):
        raise McpValidationError("command کاراکتر غیرمجاز (متاکاراکتر shell) داره.")

    stem = _stem(command)
    if stem in _FORBIDDEN_EXECUTABLES:
        raise McpValidationError(f"اجرای «{stem}» به‌عنوان command مجاز نیست (shell/اجرای دلخواه).")

    if _is_absolute(command):
        if must_exist and not os.path.isfile(command):
            raise McpValidationError("فایل اجرایی با این مسیر وجود نداره.")
        return command

    # Bare name: must be exactly an allow-listed launcher -- no path
    # separators, no spaces, no extra tokens ("npx -y foo" belongs in args).
    if any(ch in command for ch in ("/", "\\", " ", "\t")):
        raise McpValidationError(
            "command باید فقط اسم یه launcher مجاز باشه (مثل npx/uvx/python) یا مسیر کامل فایل اجرایی؛ "
            "آرگومان‌ها رو توی args بذار."
        )
    if command.lower() not in ALLOWED_LAUNCHERS:
        allowed = ", ".join(sorted(ALLOWED_LAUNCHERS))
        raise McpValidationError(f"«{command}» توی لیست launcherهای مجاز نیست ({allowed}) — یا مسیر کامل بده.")
    return command


def validate_args(args: Any, command: Optional[str] = None) -> list[str]:
    if args is None:
        return []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise McpValidationError("args باید لیستی از رشته‌ها باشه.")
    if len(args) > MAX_ARGS:
        raise McpValidationError(f"حداکثر {MAX_ARGS} آرگومان مجازه.")
    for a in args:
        if len(a) > MAX_ARG_LEN:
            raise McpValidationError("یکی از آرگومان‌ها خیلی طولانیه.")
        if "\x00" in a:
            raise McpValidationError("آرگومان نمی‌تونه کاراکتر NUL داشته باشه.")
    if command:
        flags = _INLINE_CODE_FLAGS.get(_stem(command))
        if flags:
            bad = [a for a in args if a in flags]
            if bad:
                raise McpValidationError(
                    f"اجرای کد inline ({bad[0]}) با «{_stem(command)}» مجاز نیست — یه فایل اسکریپت بده."
                )
    return list(args)


def validate_env(env: Any) -> dict[str, str]:
    if env is None:
        return {}
    if not isinstance(env, dict):
        raise McpValidationError("env باید یه دیکشنری باشه.")
    if len(env) > MAX_ENV_VARS:
        raise McpValidationError(f"حداکثر {MAX_ENV_VARS} متغیر محیطی مجازه.")
    out: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not _ENV_KEY_RE.match(key):
            raise McpValidationError(f"اسم متغیر محیطی نامعتبره: {key!r}")
        if key.upper() in _FORBIDDEN_ENV_KEYS:
            raise McpValidationError(f"تنظیم متغیر محیطی «{key}» مجاز نیست (روی resolve/بارگذاری برنامه اثر می‌ذاره).")
        if not isinstance(value, str) or "\x00" in value or len(value) > MAX_VALUE_LEN:
            raise McpValidationError(f"مقدار متغیر «{key}» نامعتبره.")
        out[key] = value
    return out


def validate_url(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        raise McpValidationError("برای transport از نوع http/sse، url لازمه.")
    url = url.strip()
    if len(url) > MAX_URL_LEN or any(ord(c) < 32 or c == " " for c in url):
        raise McpValidationError("url نامعتبره.")
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises ValueError on a bad port
    except ValueError:
        raise McpValidationError("url نامعتبره.")
    if parts.scheme not in ("http", "https"):
        raise McpValidationError("url باید با http:// یا https:// شروع بشه.")
    if not parts.hostname:
        raise McpValidationError("url هاست نداره.")
    if parts.username or parts.password:
        raise McpValidationError("اطلاعات ورود نباید توی خود url باشه — از headers استفاده کن.")
    return url


def validate_headers(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    if not isinstance(headers, dict):
        raise McpValidationError("headers باید یه دیکشنری باشه.")
    if len(headers) > MAX_ENV_VARS:
        raise McpValidationError("تعداد headerها خیلی زیاده.")
    out: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not _HEADER_NAME_RE.match(key):
            raise McpValidationError(f"اسم header نامعتبره: {key!r}")
        if not isinstance(value, str) or any(c in value for c in "\r\n\x00") or len(value) > MAX_VALUE_LEN:
            raise McpValidationError(f"مقدار header «{key}» نامعتبره.")
        out[key] = value
    return out


def is_loopback_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")


def validate_server_config(
    *,
    transport: Any,
    command: Any = None,
    args: Any = None,
    env: Any = None,
    url: Any = None,
    headers: Any = None,
) -> dict:
    """Validates a COMPLETE config for one transport and returns the
    normalized fields ready to store. Fields that belong to the other
    transport are dropped rather than kept as dead data."""
    transport = validate_transport(transport)
    if transport == "stdio":
        cmd = validate_command(command)
        return {
            "transport": transport,
            "command": cmd,
            "args": validate_args(args, cmd),
            "env": validate_env(env),
            "url": None,
            "headers": {},
        }
    return {
        "transport": transport,
        "command": None,
        "args": [],
        "env": {},
        "url": validate_url(url),
        "headers": validate_headers(headers),
    }
