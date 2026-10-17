"""
backend/core/agent_search.py

How GitHub agent search decides what to show first. Pure functions, no
network, so every rule is unit-tested.

What went wrong before: one GitHub query ("open interpreter in:name,description,
topics") was sent as typed. GitHub matches words, so a project whose name is
the single word "openinterpreter" never matched "open interpreter", and a hugely
popular agent ("hermes-agent", 250k stars) was buried under unrelated projects
that happen to share a word ("hermes" the JavaScript engine). So:

  1. The query is tried in the spellings a project name can take (as typed,
     joined, hyphenated). Name-only queries for the joined/hyphenated forms.
  2. A short list of well-known agent projects (data below, extend freely) is
     looked up directly when the query names one, so the canonical repo can
     never be missed by a search quirk. It is marked, not hidden among others.
  3. Every hit is scored from signals a person weighs by hand: how well the
     name matches, whether it describes itself as an AI agent at all, stars,
     recent activity, archived, license, owner type. The score is split into
     named parts with a written reason. Nothing is a hard filter: an unrelated
     project is pushed down, never removed.

Nothing here claims a project is safe. GitHub can't vouch for one, and the
install step still shows the exact command and asks for confirmation.
"""

from __future__ import annotations

import difflib
import math
import re
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------

# Canonical repositories of agents people look for by name. `aliases` are the
# spellings that should find it. Add a line to teach the search a new one.
KNOWN_AGENTS: tuple[dict, ...] = (
    {"repo": "openinterpreter/openinterpreter", "aliases": ("open interpreter", "openinterpreter", "open-interpreter")},
    {"repo": "NousResearch/hermes-agent", "aliases": ("hermes", "hermes agent", "hermes-agent", "nous hermes")},
    # Widely used agents/frameworks. A repository that was renamed or moved only
    # loses this direct lookup (a failed lookup is skipped); the normal search
    # still finds it.
    {"repo": "Aider-AI/aider", "aliases": ("aider", "aider chat")},
    {"repo": "block/goose", "aliases": ("goose", "goose agent", "block goose")},
    {"repo": "cline/cline", "aliases": ("cline",)},
    {"repo": "openai/codex", "aliases": ("codex", "codex cli", "openai codex")},
    {"repo": "anthropics/claude-code", "aliases": ("claude code", "claude-code", "claudecode")},
    {"repo": "continuedev/continue", "aliases": ("continue", "continue dev", "continuedev")},
    {"repo": "huggingface/smolagents", "aliases": ("smolagents", "smol agents")},
    {"repo": "crewAIInc/crewAI", "aliases": ("crewai", "crew ai")},
    {"repo": "microsoft/autogen", "aliases": ("autogen", "auto gen")},
    {"repo": "browser-use/browser-use", "aliases": ("browser use", "browser-use", "browseruse")},
)

FUZZY_MIN_LENGTH = 5  # shorter names are too easily "close" to ordinary words
FUZZY_CUTOFF = 0.8

# Words that mean "this project is an AI agent / LLM tool".
AGENT_WORDS = frozenset(
    {
        "agent", "agents", "agentic", "llm", "llms", "ai", "assistant", "copilot", "autonomous", "cli",
        "terminal", "coding", "mcp", "openai", "chatgpt", "gpt", "claude", "ollama", "llama", "gemini",
        "interpreter", "tool-use", "function-calling", "langchain", "rag",
    }
)

# Score budget.
W_NAME_EXACT = 35
W_NAME_CONTAINS = 25
W_NAME_TOKENS = 22
W_NAME_ONLY_TEXT = 8
W_AGENT_WORDS = 20
PENALTY_NOT_AN_AGENT = 15
W_STARS = 25
W_ACTIVITY = 10
PENALTY_ARCHIVED = 20
W_LICENSE = 2
W_ORG = 3
W_KNOWN = 15
STARS_FULL_AT = 100_000

# ---------------------------------------------------------------------
# Query handling
# ---------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9.]+", text.lower())


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def query_variants(query: str) -> list[dict]:
    """GitHub search queries to run, in order. Each is {"q": ..., "scope": "text"|"name"}.
    "text" searches name, description and topics; "name" only repo names."""
    q = " ".join(_tokens(query))
    toks = q.split()
    out = [{"q": q, "scope": "text"}]
    if len(toks) > 1:
        out.append({"q": "".join(toks), "scope": "name"})
        out.append({"q": "-".join(toks), "scope": "name"})
    seen: set[tuple[str, str]] = set()
    return [v for v in out if v["q"] and not ((v["q"], v["scope"]) in seen or seen.add((v["q"], v["scope"])))]


def direct_repo(query: str) -> Optional[str]:
    """`owner/name` typed (or pasted from a GitHub URL) means exactly that repo."""
    m = re.search(r"(?:github\.com/)?([A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*)/?$", query.strip())
    return m.group(1) if m and "/" in m.group(1) else None


def known_repos_for(query: str, fuzzy: bool = True) -> list[str]:
    """Canonical repos of known agents the query names (spelling-insensitive).
    A near miss ("hermis", "openinterpeter") is accepted too, because a typo in a
    name is exactly when a person can't find the project by searching."""
    qs = _squash(query)
    if not qs:
        return []
    hits = [e["repo"] for e in KNOWN_AGENTS if any(_squash(a) == qs for a in e["aliases"])]
    if hits or not fuzzy or len(qs) < FUZZY_MIN_LENGTH:
        return hits
    for entry in KNOWN_AGENTS:
        for alias in entry["aliases"]:
            sa = _squash(alias)
            if len(sa) >= FUZZY_MIN_LENGTH and difflib.SequenceMatcher(None, qs, sa).ratio() >= FUZZY_CUTOFF:
                hits.append(entry["repo"])
                break
    return hits


# ---------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------


def _days_since(iso: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, ((now or datetime.now(timezone.utc)) - t).total_seconds() / 86400.0)


def _activity_points(days: Optional[float]) -> float:
    if days is None:
        return 0.0
    if days <= 90:
        return W_ACTIVITY
    if days <= 365:
        return 6.0
    if days <= 730:
        return 3.0
    return 0.0


def name_points(full_name: str, query: str) -> tuple[float, str]:
    """(points, how the name matched)."""
    name = full_name.split("/", 1)[-1]
    ns, qs = _squash(name), _squash(query)
    if not qs:
        return 0.0, "none"
    if ns == qs:
        return W_NAME_EXACT, "exact"
    if qs in ns:
        return W_NAME_CONTAINS, "contains"
    toks = [t for t in _tokens(query) if _squash(t)]
    if toks and all(_squash(t) in ns for t in toks):
        return W_NAME_TOKENS, "tokens"
    return 0.0, "none"


def agent_likeness(item: dict) -> int:
    words: set[str] = set()
    for t in item.get("topics") or []:
        words.update(re.split(r"[^a-z0-9-]+", str(t).lower()))
    words.update(re.findall(r"[a-z0-9-]+", (item.get("description") or "").lower()))
    words.update(re.findall(r"[a-z0-9]+", item["full_name"].lower()))
    return len(words & AGENT_WORDS)


def _short(n: float) -> str:
    n = float(n)
    for div, suffix in ((1_000_000, "M"), (1_000, "k")):
        if n >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + suffix
    return str(int(n))


def score_agent(item: dict, query: str, known: bool = False, now: Optional[datetime] = None) -> dict:
    full_name = item["full_name"]
    name_pts, how = name_points(full_name, query)
    likeness = agent_likeness(item)

    text_only = 0.0
    if how == "none":
        qt = [t for t in _tokens(query) if t]
        blob = ((item.get("description") or "") + " " + " ".join(item.get("topics") or [])).lower()
        if qt and all(t in blob for t in qt):
            text_only = W_NAME_ONLY_TEXT

    days = _days_since(item.get("last_push"), now)
    stars = item.get("stars") or 0
    parts = {
        "name_match": name_pts or text_only,
        "agent_words": min(W_AGENT_WORDS, likeness * 7),
        "not_an_agent_penalty": -PENALTY_NOT_AN_AGENT if likeness == 0 else 0,
        "stars": round(W_STARS * min(1.0, math.log10(1 + stars) / math.log10(1 + STARS_FULL_AT)), 1) if stars > 0 else 0,
        "activity": _activity_points(days),
        "archived_penalty": -PENALTY_ARCHIVED if item.get("archived") else 0,
        "license": W_LICENSE if item.get("license") and item["license"] != "NOASSERTION" else 0,
        "organization": W_ORG if item.get("owner_type") == "Organization" else 0,
        "known_project": W_KNOWN if known else 0,
    }
    total = sum(parts.values())

    why: list[str] = []
    if known:
        why.append("Well-known agent project")
    if how == "exact":
        why.append("Name is exactly your search")
    elif how in ("contains", "tokens"):
        why.append("Name contains your search")
    elif text_only:
        why.append("Only the description mentions it")
    if likeness == 0:
        why.append("Doesn't describe itself as an AI agent or LLM tool")
    if stars:
        why.append(f"{_short(stars)} stars")
    if days is not None:
        if days <= 90:
            why.append("Updated in the last 3 months")
        elif days > 730:
            why.append("No updates for over 2 years")
    if item.get("archived"):
        why.append("Archived (read-only, no longer maintained)")

    out = dict(item)
    out.update({"score": round(max(total, 0.0), 1), "score_parts": parts, "why": why, "known": known, "name_match": how})
    return out


def rank(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda r: (-r["score"], r["full_name"].lower()))
