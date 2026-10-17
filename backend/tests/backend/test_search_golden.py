"""The golden search cases, the harness that runs them, and the ranking rules they exposed."""

import json
from pathlib import Path

import pytest

from backend.core import agent_search as ags
from backend.core import model_search as ms
from backend.core import search_eval as se

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "search_golden.json"


# ---------- the bundled cases are a regression guard ----------

def test_every_bundled_golden_case_passes():
    results, summary = se.evaluate_file(GOLDEN)
    failing = [f"{r.id}: {r.problem}" for r in results if not r.passed]
    assert not failing, failing
    assert summary["cases"] >= 25 and summary["hit_at_1"] == 1.0 and summary["mrr"] == 1.0


def test_agent_cases_also_pass_without_the_known_agent_list():
    """The ranker has to stand on its own; the curated list is only a safety net."""
    for case in se.load_cases(GOLDEN):
        if case["kind"] != "agent" or case["id"].startswith("agent-typo"):
            continue  # the typo cases exist to prove the list's near-name lookup
        r = se.evaluate_case({**case, "known": []})
        assert r.passed, f"{case['id']}: {r.problem}"


def test_the_bundled_file_is_well_formed():
    cases = se.load_cases(GOLDEN)
    kinds = {c["kind"] for c in cases}
    assert kinds == {"model", "agent"}
    for c in cases:
        ids = {x["repo_id"] if c["kind"] == "model" else x["full_name"] for x in c["candidates"]}
        assert set(c["good"]) <= ids, c["id"]  # a label that names nothing in the pool is a typo
        for a, b in c.get("must_beat", []):
            assert a in ids and b in ids, c["id"]


# ---------- the harness itself ----------

def model_case(**kw):
    base = {"id": "t", "kind": "model", "query": "qwen3 4b",
            "candidates": [{"repo_id": "a/Qwen3-4B-GGUF", "downloads": 10, "likes": 1},
                           {"repo_id": "b/Qwen3-14B-GGUF", "downloads": 10, "likes": 1}],
            "good": ["a/Qwen3-4B-GGUF"]}
    base.update(kw)
    return base


def test_a_passing_and_a_failing_case_are_told_apart():
    assert se.evaluate_case(model_case()).passed
    bad = se.evaluate_case(model_case(good=["b/Qwen3-14B-GGUF"]))
    assert not bad.passed and "wanted one of" in bad.problem and bad.reciprocal_rank == 0.5


def test_must_beat_pairs_are_checked_and_missing_names_count_as_broken():
    ok = se.evaluate_case(model_case(must_beat=[["a/Qwen3-4B-GGUF", "b/Qwen3-14B-GGUF"]]))
    assert ok.passed
    reversed_ = se.evaluate_case(model_case(must_beat=[["b/Qwen3-14B-GGUF", "a/Qwen3-4B-GGUF"]]))
    assert not reversed_.passed and reversed_.broken_pairs
    absent = se.evaluate_case(model_case(must_beat=[["a/Qwen3-4B-GGUF", "nobody/here"]]))
    assert not absent.passed


def test_an_unlabeled_case_does_not_count_as_passing():
    r = se.evaluate_case(model_case(good=[]))
    assert not r.passed and "unlabeled" in r.problem


def test_known_empty_list_means_not_recognised_not_use_the_default():
    case = {"id": "t", "kind": "agent", "query": "hermes", "good": ["NousResearch/hermes-agent"],
            "candidates": [{"full_name": "NousResearch/hermes-agent", "stars": 5}]}
    with_default = se.rank_case(case)[0]
    alone = se.rank_case({**case, "known": []})[0]
    assert with_default["known"] is True and alone["known"] is False


def test_loading_rejects_duplicate_ids_and_bad_kinds(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"cases": [model_case(), model_case()]}), encoding="utf-8")
    with pytest.raises(ValueError):
        se.load_cases(p)
    p.write_text(json.dumps({"cases": [model_case(kind="song")]}), encoding="utf-8")
    with pytest.raises(ValueError):
        se.load_cases(p)


def test_summary_metrics():
    rs = [se.CaseResult("a", "q", "x", True, 1.0, []), se.CaseResult("b", "q", "y", False, 0.5, [])]
    assert se.summarize(rs) == {"cases": 2, "passed": 1, "hit_at_1": 0.5, "mrr": 0.75}
    assert se.summarize([])["cases"] == 0


# ---------- model ranking rules the cases exposed ----------

def parts(repo, query, **item):
    return ms.score_repo({"repo_id": repo, "downloads": 0, "likes": 0, **item}, ms.parse_query(query))


def test_a_size_is_a_word_of_its_own_not_a_substring():
    assert ms.coverage("Qwen/Qwen3-14B-GGUF", ["qwen3", "4b"]) < ms.coverage("Qwen/Qwen3-4B-GGUF", ["qwen3", "4b"])
    assert ms.coverage("Qwen/Qwen3-4B-GGUF", ["qwen3", "4b"]) == 1.0
    assert ms.coverage("a/gemma-3-27b-it", ["gemma", "7b"]) < 1.0


def test_moe_active_size_tags_are_not_sizes():
    assert ms.coverage("unsloth/Qwen3-30B-A3B-GGUF", ["qwen3", "3b"]) < 1.0
    assert ms.coverage("unsloth/gemma-3n-E4B-it-GGUF", ["gemma", "4b"]) < 1.0
    assert ms.stated_sizes("unsloth/Qwen3-30B-A3B-GGUF") == [30.0]


def test_a_size_glued_to_the_name_still_counts():
    assert ms.coverage("x/Mistral7B-Instruct-GGUF", ["mistral", "7b"]) == 1.0
    assert ms.coverage("x/TinyLlama1.1B-Chat-GGUF", ["tinyllama", "1.1b"]) == 1.0


def test_a_size_counts_double_in_coverage():
    assert abs(ms.coverage("a/Mistral-Nemo", ["mistral", "7b"]) - 1 / 3) < 1e-9
    assert abs(ms.coverage("a/Mistral-Nemo", ["mistral", "nemo", "7b"]) - 2 / 4) < 1e-9


def test_a_name_that_states_another_size_is_penalised_and_says_so():
    wrong = parts("a/Qwen3-14B-GGUF", "qwen3 4b")
    assert wrong["score_parts"]["size_penalty"] == -ms.PENALTY_SIZE_MISMATCH
    assert any("14B" in w and "4B" in w for w in wrong["why"])
    assert parts("a/Qwen3-4B-GGUF", "qwen3 4b")["score_parts"]["size_penalty"] == 0
    # no size in the name = unknown, not wrong; no size asked = nothing to compare
    assert parts("a/Mistral-Nemo-GGUF", "mistral 7b")["score_parts"]["size_penalty"] == 0
    assert parts("a/Qwen3-14B-GGUF", "qwen3")["score_parts"]["size_penalty"] == 0


def test_size_mismatch_helper():
    assert ms.size_mismatch("a/Llama-3.1-8B", 8.0) is None
    assert ms.size_mismatch("a/Llama-3.1-70B", 8.0) == [70.0]
    assert ms.size_mismatch("a/Llama-3.1-8B-70B-merge", 70.0) is None
    assert ms.size_mismatch("a/Llama", 8.0) is None and ms.size_mismatch("a/Llama-8B", None) is None


def test_q6km_means_q6_k_because_no_such_file_exists():
    assert ms.canonical_quant("q6km") == "Q6_K"
    assert ms.canonical_quant("Q6_K_S") == "Q6_K"
    assert ms.canonical_quant("q2km") == "Q2_K"
    # real variants stay as typed
    assert ms.canonical_quant("q4km") == "Q4_K_M" and ms.canonical_quant("q5ks") == "Q5_K_S"
    assert ms.canonical_quant("q6k") == "Q6_K" and ms.canonical_quant("q8") == "Q8"


# ---------- agent ranking: near-name lookup ----------

def test_a_typo_in_a_known_agents_name_still_finds_it():
    assert ags.known_repos_for("hermis") == ["NousResearch/hermes-agent"]
    assert ags.known_repos_for("claud code") == ["anthropics/claude-code"]
    assert ags.known_repos_for("gooose") == ["block/goose"]


def test_near_name_matching_stays_out_of_ordinary_words():
    for q in ("code", "python", "aidr", "hermit-crab-recipes", "agent", "ai"):
        assert ags.known_repos_for(q) == [], q
    assert ags.known_repos_for("hermis", fuzzy=False) == []


def test_every_known_agent_entry_is_well_formed():
    import re

    seen = set()
    for e in ags.KNOWN_AGENTS:
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", e["repo"]), e
        assert e["aliases"] and e["repo"].lower() not in seen
        seen.add(e["repo"].lower())
        for a in e["aliases"]:
            assert e["repo"] in ags.known_repos_for(a, fuzzy=False), (e["repo"], a)
