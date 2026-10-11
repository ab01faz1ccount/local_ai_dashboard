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
