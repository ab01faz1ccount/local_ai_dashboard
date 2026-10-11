"""
backend/core/knowledge.py -- sourced facts for models (Hugging Face) and
agents (GitHub). The fixtures follow the shape of the real APIs' responses;
nothing here touches the network.
"""

import pytest

from backend.core import knowledge
from backend.core.net_util import DiscoveryError

README = """---
license: apache-2.0
language:
- en
---
<p align="center"><img src="logo.png"></p>

[![Badge](https://img.shields.io/x.svg)](https://x.y)

# Qwen2.5-7B-Instruct

## Introduction

Qwen2.5 is the latest series of large language models, with **improved** coding and maths ability and
long-context support up to 128K tokens.

| a | b |
|---|---|
"""

HF_INFO = {
    "id": "Qwen/Qwen2.5-7B-Instruct",
    "downloads": 1234567,
    "likes": 890,
    "lastModified": "2025-02-01T10:20:30.000Z",
    "pipeline_tag": "text-generation",
    "tags": ["transformers", "license:apache-2.0", "language:en"],
    "cardData": {
        "license": "apache-2.0",
        "language": ["en", "zh"],
        "model-index": [
            {"results": [{"task": {"type": "text-generation"}, "dataset": {"name": "MMLU"}, "metrics": [{"name": "accuracy", "value": 74.2}]}]}
        ],
    },
    "safetensors": {"total": 7615616512},
}

GGUF_INFO = {
    "id": "bartowski/Qwen2.5-7B-Instruct-GGUF",
    "downloads": 5000,
    "likes": 10,
    "lastModified": "2025-03-01T00:00:00.000Z",
    "pipeline_tag": "text-generation",
    "tags": ["gguf", "base_model:Qwen/Qwen2.5-7B-Instruct", "base_model:quantized:Qwen/Qwen2.5-7B-Instruct"],
    "cardData": {"base_model": "Qwen/Qwen2.5-7B-Instruct"},
    "gguf": {"total": 7615616512, "architecture": "qwen2", "context_length": 32768},
}


def by_key(profile):
    return {f["key"]: f for f in profile["facts"]}


# ---- text helpers --------------------------------------------------------

def test_summary_skips_front_matter_badges_headings_and_tables():
    s = knowledge.extract_summary(README)
    assert s.startswith("Qwen2.5 is the latest series")
    assert "**" not in s and "<" not in s


def test_summary_none_when_there_is_no_real_paragraph():
    assert knowledge.extract_summary("# Title\n\n![x](y.png)\n\n| a |\n") is None
    assert knowledge.extract_summary("") is None


def test_summary_is_cut_on_a_word_boundary():
    s = knowledge.extract_summary("word " * 200, limit=50)
    assert len(s) <= 51 and s.endswith("…") and not s.endswith(" …")


FEATURES = """# Agent

An agent that does things for you in your terminal every day.

## ✨ Features

- **Runs code** in a sandbox with [Python](https://python.org)
- Reads and edits files
* Browses the web
- x

## Install

- pip install agent
"""


def test_capabilities_come_from_the_features_section_only():
    caps = knowledge.extract_capabilities(FEATURES)
    assert caps == ["Runs code in a sandbox with Python", "Reads and edits files", "Browses the web"]


def test_capabilities_empty_without_a_features_section():
    assert knowledge.extract_capabilities("# A\n\n- pip install\n- run it\n") == []
    assert knowledge.extract_capabilities("") == []


def test_capabilities_stop_at_the_next_heading_of_the_same_level():
    md = "## Features\n- first thing here\n## Usage\n- second thing here\n"
    assert knowledge.extract_capabilities(md) == ["first thing here"]


def test_capabilities_are_capped():
    md = "## Features\n" + "".join(f"- feature number {i}\n" for i in range(30))
    assert len(knowledge.extract_capabilities(md, limit=8)) == 8


# ---- fact() ---------------------------------------------------------------

def test_an_empty_value_is_always_missing_never_blank():
    for empty in (None, "", []):
        f = knowledge.fact("k", "K", empty)
        assert f["provenance"] == "missing" and f["value"] is None and f["note"]


# ---- model profile --------------------------------------------------------

def test_original_model_card_facts_are_attributed_to_its_card():
    p = knowledge.build_model_profile("Qwen/Qwen2.5-7B-Instruct", HF_INFO, README)
    f = by_key(p)
    assert f["license"]["value"] == "apache-2.0" and f["license"]["provenance"] == "source"
    assert f["license"]["source"]["url"] == "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct"
    assert f["parameters"]["value"] == "7.6B"
    assert f["languages"]["value"] == "en, zh"
    assert f["downloads"]["value"] == "1,234,567"
    assert f["updated"]["value"] == "2025-02-01"
    assert f["benchmarks"]["value"] == [{"dataset": "MMLU", "metric": "accuracy", "value": 74.2}]
    assert f["description"]["value"].startswith("Qwen2.5 is")


def test_a_quant_repo_fills_gaps_from_its_base_model_and_says_so():
    p = knowledge.build_model_profile(
        "bartowski/Qwen2.5-7B-Instruct-GGUF", GGUF_INFO, None, base=("Qwen/Qwen2.5-7B-Instruct", HF_INFO, README),
        filename="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
    )
    f = by_key(p)
    assert f["architecture"]["provenance"] == "source" and f["architecture"]["value"] == "qwen2"
    assert f["context_length"]["value"] == "32,768 tokens"
    assert f["license"]["provenance"] == "base_model" and f["license"]["value"] == "apache-2.0"
    assert "Qwen/Qwen2.5-7B-Instruct" in f["license"]["note"]
    assert f["license"]["source"]["url"].endswith("Qwen/Qwen2.5-7B-Instruct")
    assert f["benchmarks"]["provenance"] == "base_model"
    assert f["description"]["provenance"] == "base_model"
    assert f["base_model"]["value"] == "Qwen/Qwen2.5-7B-Instruct"
    assert f["quantization"]["value"] == "Q4_K_M" and f["quantization"]["provenance"] == "derived"
    assert len(p["sources"]) == 2


def test_nothing_published_is_stated_as_such_not_invented():
    p = knowledge.build_model_profile("someone/mystery-model", {"id": "someone/mystery-model", "tags": []}, None)
    f = by_key(p)
    for key in ("license", "architecture", "context_length", "languages", "benchmarks", "description"):
        assert f[key]["provenance"] == "missing" and f[key]["value"] is None, key
        assert f[key]["note"]
    assert f["downloads"]["provenance"] == "missing"


def test_parameters_are_guessed_from_the_name_only_when_the_card_is_silent_and_labelled():
    p = knowledge.build_model_profile("someone/Mistral-7B-v0.3", {"id": "x", "tags": []}, None)
    f = by_key(p)["parameters"]
    assert f["value"] == "7B" and f["provenance"] == "derived"
    p2 = knowledge.build_model_profile("someone/Mistral-7B-v0.3", HF_INFO, None)
    assert by_key(p2)["parameters"]["provenance"] == "source"  # the card's number wins over the guess


def test_license_and_languages_fall_back_to_tags():
    info = {"id": "x/y", "tags": ["license:mit", "language:fr", "language:de"]}
    f = by_key(knowledge.build_model_profile("x/y", info, None))
    assert f["license"]["value"] == "mit"
    assert f["languages"]["value"] == "fr, de"


def test_base_model_can_come_from_tags_and_junk_is_ignored():
    info = {"tags": ["base_model:quantized:meta-llama/Llama-3.1-8B"], "cardData": {"base_model": ["not a repo", 5]}}
    assert knowledge.parse_model_info(info)["base_models"] == ["meta-llama/Llama-3.1-8B"]


def test_evals_without_a_value_are_dropped():
    info = {"cardData": {"model-index": [{"results": [{"dataset": {"name": "A"}, "metrics": [{"name": "m", "value": None}, {"name": "n", "value": 1}]}]}]}}
    assert knowledge.parse_model_info(info)["evals"] == [{"dataset": "A", "metric": "n", "value": 1}]


def test_garbage_shapes_do_not_crash_the_parser():
    knowledge.parse_model_info({"cardData": {"model-index": "nope", "language": 5}, "gguf": None, "tags": [1, None]})
    knowledge.build_model_profile("a/b", {}, "")


# ---- agent profile --------------------------------------------------------

GH_INFO = {
    "html_url": "https://github.com/acme/agent",
    "description": "Let language models run code on your computer.",
    "license": {"spdx_id": "MIT"},
    "language": "Python",
    "stargazers_count": 60500,
    "forks_count": 5200,
    "pushed_at": "2026-01-03T12:00:00Z",
    "homepage": "https://acme.dev",
    "topics": ["llm", "agent"],
    "archived": False,
}


def test_agent_profile_facts_and_sources():
    p = knowledge.build_agent_profile("acme/agent", GH_INFO, FEATURES, {"tag_name": "v1.2.0", "published_at": "2026-01-01T00:00:00Z"})
    f = by_key(p)
    assert f["license"]["value"] == "MIT"
    assert f["stars"]["value"] == "60,500"
    assert f["pushed"]["value"] == "2026-01-03"
    assert f["release"]["value"] == "v1.2.0 (2026-01-01)"
    assert f["capabilities"]["value"][0] == "Runs code in a sandbox with Python"
    assert f["capabilities"]["source"]["url"].endswith("#readme")
    assert "archived" not in f


def test_agent_without_license_release_or_features_says_so():
    info = {**GH_INFO, "license": {"spdx_id": "NOASSERTION"}, "homepage": ""}
    f = by_key(knowledge.build_agent_profile("acme/agent", info, "# A\n", None))
    assert f["license"]["provenance"] == "missing"
    assert f["release"]["provenance"] == "missing" and "No published releases" in f["release"]["note"]
    assert f["capabilities"]["provenance"] == "missing" and "llm, agent" in f["capabilities"]["note"]
    assert f["homepage"]["provenance"] == "missing"


def test_archived_repo_is_flagged():
    f = by_key(knowledge.build_agent_profile("acme/agent", {**GH_INFO, "archived": True}, None, None))
    assert "Archived" in f["archived"]["value"]


# ---- fetching (network stubbed) -------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    knowledge._cache.clear()
    yield
    knowledge._cache.clear()


def test_fetch_model_profile_pulls_the_base_card_and_caches(monkeypatch):
    calls = []

    def fake_json(url, **kw):
        calls.append(url)
        return GGUF_INFO if "bartowski" in url else HF_INFO

    monkeypatch.setattr(knowledge, "get_json", fake_json)
    monkeypatch.setattr(knowledge, "get_text", lambda url, **kw: README)
    p = knowledge.fetch_model_profile("bartowski/Qwen2.5-7B-Instruct-GGUF", "Qwen2.5-7B-Instruct-Q4_K_M.gguf")
    assert by_key(p)["license"]["provenance"] == "base_model"
    n = len(calls)
    knowledge.fetch_model_profile("bartowski/Qwen2.5-7B-Instruct-GGUF", "Qwen2.5-7B-Instruct-Q4_K_M.gguf")
    assert len(calls) == n  # second call served from the cache


def test_a_missing_base_card_is_not_an_error(monkeypatch):
    def fake_json(url, **kw):
        if "bartowski" in url:
            return GGUF_INFO
        raise DiscoveryError("nf", "not_found")

    monkeypatch.setattr(knowledge, "get_json", fake_json)
    monkeypatch.setattr(knowledge, "get_text", lambda url, **kw: (_ for _ in ()).throw(DiscoveryError("nf", "not_found")))
    p = knowledge.fetch_model_profile("bartowski/Qwen2.5-7B-Instruct-GGUF")
    assert by_key(p)["architecture"]["value"] == "qwen2"
    assert by_key(p)["license"]["provenance"] == "missing"


def test_the_model_card_itself_missing_is_a_real_error(monkeypatch):
    def boom(url, **kw):
        raise DiscoveryError("nf", "not_found")

    monkeypatch.setattr(knowledge, "get_json", boom)
    with pytest.raises(DiscoveryError):
        knowledge.fetch_model_profile("a/b")


def test_bad_repo_names_are_refused_before_any_request(monkeypatch):
    monkeypatch.setattr(knowledge, "get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")))
    for bad in ("nope", "../../etc/passwd", "a/b/c", "a/b?x=1"):
        with pytest.raises(DiscoveryError):
            knowledge.fetch_model_profile(bad)
        with pytest.raises(DiscoveryError):
            knowledge.fetch_agent_profile(bad)


def test_fetch_agent_profile_tolerates_no_readme_and_no_release(monkeypatch):
    def fake_json(url, **kw):
        if url.endswith("/releases/latest"):
            raise DiscoveryError("nf", "not_found")
        return GH_INFO

    monkeypatch.setattr(knowledge, "get_json", fake_json)
    monkeypatch.setattr(knowledge, "get_text", lambda url, **kw: (_ for _ in ()).throw(DiscoveryError("nf", "not_found")))
    f = by_key(knowledge.fetch_agent_profile("acme/agent"))
    assert f["stars"]["value"] == "60,500" and f["release"]["provenance"] == "missing"


def test_summary_skips_a_short_tagline_for_the_first_real_paragraph():
    md = "Fast and tiny.\n\nThis project is a small and fast inference server that runs language models on ordinary laptops."
    assert knowledge.extract_summary(md).startswith("This project is a small")


# ---- finding the repo of a local model ----------------------------------------

@pytest.mark.parametrize(
    "name,expected",
    [
        ("Qwen2.5-7B-Instruct-Q4_K_M.gguf", "Qwen2.5 7B Instruct"),
        ("Llama-3.2-3B-Instruct-Q8_0", "Llama 3.2 3B Instruct"),
        ("gemma-2-9b-it-IQ4_XS.gguf", "gemma 2 9b it"),
        ("Big-Model-00001-of-00004.gguf", "Big Model"),
        ("plain", "plain"),
        (".gguf", ""),
    ],
)
def test_clean_model_query(name, expected):
    assert knowledge.clean_model_query(name) == expected


def _hit(repo, relevance, gated=False):
    return {"repo_id": repo, "relevance": relevance, "gated": gated}


def test_find_model_repo_takes_only_a_match_with_every_word(monkeypatch):
    monkeypatch.setattr(knowledge.model_discovery, "search_models", lambda q, limit=5: [_hit("a/partial", 0.67), _hit("b/Qwen2.5-7B-Instruct-GGUF", 1.0)])
    assert knowledge.find_model_repo("Qwen2.5-7B-Instruct-Q4_K_M.gguf")["repo_id"] == "b/Qwen2.5-7B-Instruct-GGUF"


def test_find_model_repo_refuses_a_near_miss_and_gated_repos(monkeypatch):
    monkeypatch.setattr(knowledge.model_discovery, "search_models", lambda q, limit=5: [_hit("a/partial", 0.67), _hit("g/gated", 1.0, gated=True)])
    assert knowledge.find_model_repo("Some-Model-7B") is None


def test_find_model_repo_with_nothing_to_search_does_not_call_the_network(monkeypatch):
    monkeypatch.setattr(knowledge.model_discovery, "search_models", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no")))
    assert knowledge.find_model_repo(".gguf") is None
