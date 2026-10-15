"""Online model search: understanding the query, and ranking with trust signals."""

from backend.core import model_discovery as md
from backend.core import model_search as ms
from backend.core.net_util import DiscoveryError


# ---------- understanding the query ----------

def test_quant_is_pulled_out_and_normalised():
    for text in ("qwen3.5-9B-Q6KM", "qwen3.5 9b q6_k_m", "Qwen3.5-9B Q6-K-M"):
        p = ms.parse_query(text)
        assert p["quant"] == "Q6_K_M", text
        assert p["tokens"] == ["qwen3.5", "9b"], text
    assert ms.parse_query("llama 3 8b q8")["quant"] == "Q8"
    assert ms.parse_query("llama 3 8b q8_0")["quant"] == "Q8_0"
    assert ms.parse_query("llama 3 8b q4k")["quant"] == "Q4_K"
    assert ms.parse_query("gemma 2b bf16")["quant"] == "BF16"
    assert ms.parse_query("mistral iq3_xs")["quant"] == "IQ3_XS"


def test_words_that_only_look_like_quants_are_left_alone():
    p = ms.parse_query("qwen3 qwq 32b")
    assert p["quant"] is None and p["tokens"] == ["qwen3", "qwq", "32b"]
    assert ms.parse_query("llama 3.2 3b")["quant"] is None


def test_size_is_read_but_moe_tags_are_not_a_size():
    assert ms.parse_query("qwen3.5-9B")["size_b"] == 9.0
    assert ms.parse_query("llama 3.2 3b")["size_b"] == 3.0
    assert ms.parse_query("qwen3 35B-A3B")["size_b"] == 35.0
    assert ms.parse_query("hermes")["size_b"] is None


def test_typo_in_a_family_name_is_repaired_only_against_known_families():
    assert ms.correct_tokens(["qwwn3.5", "9b"]) == (["qwen3.5", "9b"], True)
    assert ms.correct_tokens(["llma", "3"]) == (["llama", "3"], True)
    assert ms.correct_tokens(["qwen3.5", "9b"]) == (["qwen3.5", "9b"], False)
    assert ms.correct_tokens(["banana"]) == (["banana"], False)  # unrelated words stay


# ---------- reading names ----------

def test_extra_words_find_modified_versions():
    toks = ["qwen3.5", "9b"]
    assert ms.extra_words("lmstudio-community/Qwen3.5-9B-GGUF", toks) == []
    assert ms.extra_words("bartowski/Qwen3.5-9B-Instruct-GGUF", toks) == []
    assert ms.extra_words("jc-builds/Qwen3.5-9B-Q4_K_M-GGUF", toks) == []
    assert ms.extra_words("Manojb/Qwen3.5-9B-abliterated-vision-Q4_K_M.gguf", toks) == ["abliterated", "vision"]
    assert ms.extra_words("x/Qwen3.6-35B-A3B-Genesis", ["qwen3.6", "35b"]) == ["genesis"]  # A3B is not an extra


def test_group_key_ignores_publisher_quant_and_boilerplate():
    keys = {ms.group_key(r) for r in (
        "lmstudio-community/Qwen3.5-9B-GGUF", "bartowski/Qwen3.5-9B-Instruct-GGUF", "jc-builds/Qwen3.5-9B-Q4_K_M-GGUF")}
    assert len(keys) == 1
    assert ms.group_key("x/Qwen3.5-9B-heretic-GGUF") not in keys


# ---------- ranking ----------

def _hit(repo, downloads=0, likes=0, gated=False):
    return {"repo_id": repo, "author": repo.split("/")[0], "name": repo.split("/")[1],
            "downloads": downloads, "likes": likes, "updated": None, "gated": gated, "url": "u", "relevance": 1.0}


def _rank(hits, query, followers=None, purpose=None):
    p = ms.parse_query(query)
    followers = followers or {}
    return ms.rank([ms.score_repo(h, p, followers.get(h["author"]), purpose) for h in hits])


def test_trusted_publisher_beats_popular_modified_fine_tune():
    """The case that prompted this: lmstudio-community was buried under fine-tunes."""
    hits = [
        _hit("jc-builds/Qwen3.5-9B-VLM-Q4_K_M-GGUF", 8800, 16),
        _hit("Manojb/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf", 5700, 17),
        _hit("lmstudio-community/Qwen3.5-9B-GGUF", 3000, 40),
    ]
    out = _rank(hits, "qwen3.5-9B-Q4_K_M")
    assert out[0]["repo_id"] == "lmstudio-community/Qwen3.5-9B-GGUF"
    assert out[0]["role"] == "conversion" and out[0]["trust"]["tier"] == "quantizer"
    assert next(r for r in out if r["repo_id"].startswith("Manojb"))["role"] == "variant"


def test_an_unknown_publisher_is_not_hidden_or_marked_down_for_being_unknown():
    out = _rank([_hit("nobody/Llama-3.2-3B-GGUF", 100, 1)], "llama 3.2 3b")
    assert out[0]["trust"]["tier"] == "unknown" and out[0]["score_parts"]["publisher"] == 0
    assert out[0]["score"] > 40  # still well above a non-match


def test_more_downloads_and_likes_raise_the_score_among_equals():
    out = _rank([_hit("a/Llama-3.2-3B-GGUF", 10, 0), _hit("b/Llama-3.2-3B-GGUF", 900_000, 2000)], "llama 3.2 3b")
    assert out[0]["author"] == "b"
    assert out[0]["score_parts"]["downloads"] > out[1]["score_parts"]["downloads"]


def test_followers_add_trust_but_share_the_tier_budget():
    h = [_hit("a/Llama-3.2-3B-GGUF", 100, 1)]
    plain = _rank(h, "llama 3.2 3b")[0]["score"]
    famous = _rank(h, "llama 3.2 3b", {"a": 50_000})[0]["score"]
    assert famous > plain
    big = _rank([_hit("lmstudio-community/Llama-3.2-3B-GGUF", 100, 1)], "llama 3.2 3b", {"lmstudio-community": 10**7})[0]
    assert big["score_parts"]["trust_total"] == ms.W_TRUST_CAP  # 16 + 10 would be 26


def test_each_extra_word_costs_points_up_to_a_cap():
    clean = _rank([_hit("a/Llama-3.2-3B-GGUF", 100, 1)], "llama 3.2 3b")[0]
    one = _rank([_hit("a/Llama-3.2-3B-heretic-GGUF", 100, 1)], "llama 3.2 3b")[0]
    many = _rank([_hit("a/Llama-3.2-3B-a-b-c-d-e-f-GGUF", 100, 1)], "llama 3.2 3b")[0]
    assert round(clean["score"] - one["score"], 1) == 5.0
    assert round(clean["score"] - many["score"], 1) == ms.PENALTY_EXTRA_CAP


def test_a_different_quant_in_the_name_earns_no_quant_bonus():
    other = _rank([_hit("a/Qwen2.5-7B-Q8_0-GGUF", 1)], "qwen2.5 7b q4_k_m")[0]
    assert other["score_parts"]["quant_in_name"] == 0


def test_quant_in_the_name_and_purpose_only_add_points():
    hits = [_hit("a/Qwen2.5-7B-Instruct-GGUF", 100, 1), _hit("b/Qwen2.5-Coder-7B-GGUF", 100, 1)]
    base = {r["author"]: r for r in _rank(hits, "qwen2.5 7b")}
    coding = {r["author"]: r for r in _rank(hits, "qwen2.5 7b", purpose="coding")}
    assert coding["b"]["score"] - base["b"]["score"] == ms.W_PURPOSE
    assert coding["a"]["score"] == base["a"]["score"]  # never marked down
    named = _rank([_hit("a/Qwen2.5-7B-Q4_K_M-GGUF", 1)], "qwen2.5 7b q4_k_m")[0]
    assert named["score_parts"]["quant_in_name"] == ms.W_QUANT_IN_NAME


def test_gated_costs_points_and_the_reasons_are_written_out():
    g = _rank([_hit("meta-llama/Llama-3.2-3B-GGUF", 5, 1, gated=True)], "llama 3.2 3b")[0]
    assert g["score_parts"]["gated_penalty"] == -ms.PENALTY_GATED
    assert "Made by the model's own team" in g["why"]
    assert "5 downloads" in g["why"]


def test_rank_is_stable_on_ties():
    out = _rank([_hit("b/X-1B", 5, 0), _hit("a/X-1B", 5, 0)], "x 1b")
    assert [r["repo_id"] for r in out] == ["a/X-1B", "b/X-1B"]


def test_fit_hint_uses_the_name_size_and_the_shared_fit_rules():
    class Gpu:
        available = True
        vram_total_mb = 8192

    class Mem:
        total_mb = 16384

    hw = {"gpu": Gpu(), "memory": Mem(), "cpu": None}
    h9 = ms.fit_hint(9.0, hw)
    assert h9 and h9["approx_q4_bytes"] == int(9e9 * 0.6)
    assert ms.fit_hint(None, hw) is None and ms.fit_hint(9.0, None) is None
    big = ms.fit_hint(70.0, hw)
    assert big["label"] != h9["label"]


# ---------- end to end with the network faked ----------

def _item(repo, downloads=0, likes=0, **kw):
    return {"id": repo, "downloads": downloads, "likes": likes, "lastModified": "2026-01-01T00:00:00Z", **kw}


def test_full_search_drops_the_quant_from_what_is_sent_and_ranks_by_trust(monkeypatch):
    urls = []

    def fake(url, **kw):
        urls.append(url)
        if "/overview" in url:
            return {"numFollowers": 4200} if "/organizations/lmstudio-community/" in url else {}
        return [
            _item("jc-builds/Qwen3.5-9B-VLM-Q4_K_M-GGUF", 8800, 16),
            _item("lmstudio-community/Qwen3.5-9B-GGUF", 3000, 40),
        ]

    monkeypatch.setattr(md, "get_json", fake)
    md._follower_cache.clear()
    out = md.search_models_full("qwen3.5-9B-Q4_K_M")
    assert "q4" not in urls[0].lower() and "search=qwen3.5%209b" in urls[0]
    assert out["query"]["quant"] == "Q4_K_M" and out["query"]["size_b"] == 9.0
    assert out["results"][0]["repo_id"] == "lmstudio-community/Qwen3.5-9B-GGUF"
    assert out["results"][0]["trust"]["followers"] == 4200
    assert out["results"][1]["trust"]["followers"] is None  # not published -> None, never invented


def test_typo_is_retried_with_the_repaired_name(monkeypatch):
    seen = []

    def fake(url, **kw):
        seen.append(url)
        return [] if "qwwn" in url else [_item("a/Qwen3.5-9B-GGUF", 10)]

    monkeypatch.setattr(md, "get_json", fake)
    out = md.search_models_full("qwwn3.5-9B", with_followers=False)
    assert out["query"]["corrected_from"] == "qwwn3.5 9b"
    assert out["results"][0]["repo_id"] == "a/Qwen3.5-9B-GGUF"


def test_follower_lookup_failure_never_breaks_search(monkeypatch):
    def fake(url, **kw):
        if "/overview" in url:
            raise DiscoveryError("nope", "network")
        return [_item("a/Llama-3.2-3B-GGUF", 10)]

    monkeypatch.setattr(md, "get_json", fake)
    md._follower_cache.clear()
    out = md.search_models_full("llama 3.2 3b")
    assert out["results"][0]["trust"]["followers"] is None


def test_follower_values_that_are_not_plain_numbers_are_ignored(monkeypatch):
    md._follower_cache.clear()
    monkeypatch.setattr(md, "get_json", lambda url, **kw: {"numFollowers": "lots"})
    assert md.fetch_followers("someone") is None
    md._follower_cache.clear()
    monkeypatch.setattr(md, "get_json", lambda url, **kw: {"numFollowers": True})
    assert md.fetch_followers("someone") is None


def test_only_a_quant_and_no_name_is_refused():
    try:
        md.search_models_full("Q4_K_M")
    except DiscoveryError as e:
        assert e.kind == "bad_input"
    else:
        raise AssertionError("expected DiscoveryError")


def test_the_reason_never_hides_that_more_words_exist_than_it_lists():
    r = _rank([_hit("a/Qwen3.5-text-9B-NSFW-RP-RolePlay-GGUF", 1)], "qwen3.5 9b")[0]
    assert len(r["extra_words"]) > 3
    assert "…" in r["why"][0]
    short = _rank([_hit("a/Qwen3.5-9B-heretic-GGUF", 1)], "qwen3.5 9b")[0]
    assert "…" not in short["why"][0]
