"""
backend/core/agents/toml_edit.py

Just enough TOML to edit an agent's config file safely.

Python can READ TOML (`tomllib`) but not write it, and the agents that keep a
`config.toml` (Open Interpreter) have no `config set` command to call. So the
adapter reads the file into a dict, changes only its own keys, and writes the
whole thing back. That is only safe if the writer is exact -- so it supports
the plain subset (strings, bools, ints, floats, lists of those, nested tables)
and REFUSES anything else (arrays of tables, dates) instead of guessing:
a config we can't rewrite faithfully is left untouched and reported.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


class UnsupportedToml(ValueError):
    """The document uses TOML we can't write back without risking changes."""


def loads(text: str) -> dict:
    if tomllib is None:
        raise RuntimeError("Python 3.11+ لازم است (tomllib) تا config.toml خوانده شود.")
    return tomllib.loads(text)


def _key(k: str) -> str:
    return k if _BARE_KEY.match(k) else json.dumps(k)


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            raise UnsupportedToml("nan/inf")
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # a valid TOML basic string
    raise UnsupportedToml(f"unsupported value type {type(v).__name__}")


def _value(v: Any) -> str:
    if isinstance(v, list):
        if any(isinstance(x, (dict, list)) for x in v):
            raise UnsupportedToml("nested arrays / arrays of tables")
        return "[" + ", ".join(_scalar(x) for x in v) + "]"
    return _scalar(v)


def _emit(table: dict, path: list[str], out: list[str]) -> None:
    scalars = {k: v for k, v in table.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in table.items() if isinstance(v, dict)}
    if path and (scalars or not tables):
        out.append("[" + ".".join(_key(p) for p in path) + "]")
    for k, v in scalars.items():
        out.append(f"{_key(k)} = {_value(v)}")
    if path and (scalars or not tables):
        out.append("")
    for k, v in tables.items():
        _emit(v, path + [k], out)


def dumps(data: dict) -> str:
    """Serialize. Top-level scalars come first (TOML requires it), then tables."""
    out: list[str] = []
    top = {k: v for k, v in data.items() if not isinstance(v, dict)}
    for k, v in top.items():
        out.append(f"{_key(k)} = {_value(v)}")
    if top:
        out.append("")
    for k, v in data.items():
        if isinstance(v, dict):
            _emit(v, [k], out)
    text = "\n".join(out).rstrip() + "\n"
    # never write something we can't read back identically (e.g. a raw DEL
    # character is not legal inside a TOML string, and json.dumps leaves it raw)
    try:
        round_trip = loads(text)
    except ValueError as exc:
        raise UnsupportedToml(f"not valid TOML: {exc}") from exc
    if round_trip != data:
        raise UnsupportedToml("round-trip mismatch")
    return text


def set_dotted(data: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = data
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def get_dotted(data: dict, dotted: str) -> Any:
    cur: Any = data
    for p in dotted.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur
