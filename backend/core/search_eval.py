"""
backend/core/search_eval.py

A golden-query check for the two rankers (core/model_search.py and
core/agent_search.py). No network: every case carries its own pool of
candidates (what the source returned for that query), plus what a careful
person would pick. The check ranks the pool with the real scoring code and
reports where the right answers landed.

Why: the scoring weights were set by hand. Without cases that say "for THIS
query THIS result should come first", a change that helps one search silently
breaks another. These cases are that memory.

Case format (JSON; see tests/fixtures/search_golden.json):

  {"id": "qwen35-9b-q6", "kind": "model" | "agent", "query": "...",
   "purpose": "coding",                      # model only, optional
   "followers": {"unsloth": 12000},          # model only, optional: publisher -> followers
   "known": ["owner/name"],                  # agent only: canonical repos the app looks up
   "candidates": [ {model: repo_id/downloads/likes/gated | agent: full_name/stars/...} ],
   "good": ["id", ...],                      # acceptable #1 answers; the top result must be one of them
   "must_beat": [["a", "b"], ...],           # a must rank above b
   "note": "why"}

A case is a PASS when the top result is in `good` AND every must_beat pair holds.
Metrics: hit@1 (top result is good), MRR of the first good result, and the
list of broken pairs.

The candidate pools are snapshots: a pool captured with
scripts/capture_search_case.py on a real machine is worth more than one
written by hand, because it holds what the sources really returned.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import agent_search, model_search

# "now" for activity scoring, so a stored snapshot ranks the same next year.
DEFAULT_NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _id(case: dict, item: dict) -> str:
    return item["repo_id"] if case["kind"] == "model" else item["full_name"]


def rank_case(case: dict, now: datetime = DEFAULT_NOW) -> list[dict]:
    """The pool, ranked the way the app would rank it."""
    if case["kind"] == "model":
        parsed = model_search.parse_query(case["query"])
        if case.get("typo_fix"):
            fixed, changed = model_search.correct_tokens(parsed["tokens"])
            if changed:
                parsed = {**parsed, "tokens": fixed, "text": " ".join(fixed)}
        followers = case.get("followers") or {}
        items = [{"author": c["repo_id"].split("/", 1)[0], **c} for c in case["candidates"]]
        return model_search.rank(
            [model_search.score_repo(i, parsed, followers.get(i["author"]), case.get("purpose")) for i in items]
        )
    declared = case.get("known")  # [] means "the app did not recognise this name"
    known = {r.lower() for r in (agent_search.known_repos_for(case["query"]) if declared is None else declared)}
    return agent_search.rank(
        [agent_search.score_agent(c, case["query"], known=c["full_name"].lower() in known, now=now)
         for c in case["candidates"]]
    )


@dataclass
class CaseResult:
    id: str
    query: str
    top: str
    passed: bool
    reciprocal_rank: float
    ranking: list[str]
    broken_pairs: list[tuple[str, str]] = field(default_factory=list)
    problem: str = ""


def evaluate_case(case: dict, now: datetime = DEFAULT_NOW) -> CaseResult:
    ranked = rank_case(case, now)
    ids = [_id(case, r) for r in ranked]
    good = list(case.get("good") or [])
    first_good = next((i for i, x in enumerate(ids) if x in good), None)
    rr = 0.0 if first_good is None else 1.0 / (first_good + 1)
    broken = []
    for a, b in case.get("must_beat") or []:
        if a not in ids or b not in ids:
            broken.append((a, b))  # a case that names something absent is itself broken
        elif ids.index(a) > ids.index(b):
            broken.append((a, b))
    top_ok = bool(ids) and ids[0] in good
    problem = ""
    if not good:
        problem = "no `good` answer filled in yet (an unlabeled case proves nothing)"
    elif not top_ok:
        problem = f"top result is {ids[0] if ids else '(none)'}, wanted one of {good}"
    elif broken:
        problem = "order broken: " + ", ".join(f"{a} should beat {b}" for a, b in broken)
    return CaseResult(case["id"], case["query"], ids[0] if ids else "", top_ok and not broken, rr, ids, broken, problem)


def load_cases(path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    seen: set[str] = set()
    for c in cases:
        if c["id"] in seen:
            raise ValueError(f"duplicate case id {c['id']}")
        seen.add(c["id"])
        if c["kind"] not in ("model", "agent"):
            raise ValueError(f"{c['id']}: kind must be model or agent")
    return cases


def summarize(results: list[CaseResult]) -> dict:
    n = len(results)
    return {
        "cases": n,
        "passed": sum(r.passed for r in results),
        "hit_at_1": round(sum(1 for r in results if r.reciprocal_rank == 1.0) / n, 3) if n else 0.0,
        "mrr": round(sum(r.reciprocal_rank for r in results) / n, 3) if n else 0.0,
    }


def evaluate_file(path, now: datetime = DEFAULT_NOW) -> tuple[list[CaseResult], dict]:
    results = [evaluate_case(c, now) for c in load_cases(path)]
    return results, summarize(results)
