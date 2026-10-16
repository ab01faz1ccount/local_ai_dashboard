"""comparison.sourced_profile_for_model: linked / suggested / none / error, and it never raises."""

from types import SimpleNamespace

from backend.core import comparison, knowledge
from backend.core.net_util import DiscoveryError


def model(**kw):
    base = dict(name="Qwen2.5-7B-Instruct-Q4_K_M", hf_repo_id=None, hf_filename=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_a_linked_model_is_looked_up_by_its_repo(monkeypatch):
    monkeypatch.setattr(knowledge, "fetch_model_profile", lambda repo, fn=None: {"id": repo})
    r = comparison.sourced_profile_for_model(model(hf_repo_id="a/b", hf_filename="x.gguf"))
    assert r == {"status": "linked", "repo": "a/b", "profile": {"id": "a/b"}, "error": None}


def test_an_unlinked_model_gets_a_suggestion_marked_as_such(monkeypatch):
    monkeypatch.setattr(knowledge, "find_model_repo", lambda name: {"repo_id": "c/Found"})
    monkeypatch.setattr(knowledge, "fetch_model_profile", lambda repo, fn=None: {"id": repo})
    r = comparison.sourced_profile_for_model(model())
    assert r["status"] == "suggested" and r["repo"] == "c/Found"


def test_no_match_is_none_not_an_error(monkeypatch):
    monkeypatch.setattr(knowledge, "find_model_repo", lambda name: None)
    assert comparison.sourced_profile_for_model(model())["status"] == "none"


def test_a_failed_lookup_is_reported_not_raised(monkeypatch):
    def boom(repo, fn=None):
        raise DiscoveryError("rate limited", "rate")

    monkeypatch.setattr(knowledge, "fetch_model_profile", boom)
    r = comparison.sourced_profile_for_model(model(hf_repo_id="a/b"))
    assert r["status"] == "error" and r["error"] == "rate limited" and r["profile"] is None


# ---- catalog fields filled from the file itself ----

def cat_model(**kw):
    base = dict(id=1, name="m", file_size_bytes=100, quantization=None, param_count=None, context_length=None, hf_repo_id="o/r")
    base.update(kw)
    return SimpleNamespace(**base)


def test_blank_catalog_fields_are_filled_from_the_file():
    md = comparison._metadata_dict(
        cat_model(), {"file_type": "Q6_K", "parameters_label": "8.19 B", "context_length": 262144}
    )
    assert (md["quantization"], md["param_count"], md["context_length"]) == ("Q6_K", "8.19 B", 262144)
    assert md["sources"] == {"quantization": "file", "param_count": "file", "context_length": "file"}


def test_the_file_wins_over_a_guess_from_the_repo_name():
    md = comparison._metadata_dict(cat_model(param_count="9B", quantization="Q4_K_M"), {"parameters_label": "8.95 B", "file_type": "Q4_K_M"})
    assert md["param_count"] == "8.95 B" and md["sources"]["param_count"] == "file"


def test_without_readable_file_facts_the_catalog_values_stay_and_say_so():
    md = comparison._metadata_dict(cat_model(param_count="9B"), None)
    assert md["param_count"] == "9B" and md["sources"] == {"quantization": None, "param_count": "catalog", "context_length": None}
    assert md["quantization"] is None and md["context_length"] is None


def test_partial_file_facts_only_replace_what_they_know():
    md = comparison._metadata_dict(cat_model(quantization="Q4_K_M", context_length=4096), {"parameters_label": "1.00 B"})
    assert md["quantization"] == "Q4_K_M" and md["context_length"] == 4096 and md["param_count"] == "1.00 B"
    assert md["sources"] == {"quantization": "catalog", "param_count": "file", "context_length": "catalog"}
