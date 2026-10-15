"""
backend/core/model_search.py

How online model search decides what to show first. Pure functions, no
network, so every rule is unit-tested.

Why this exists: Hugging Face's `search=` only matches the repo id as text.
Typing "qwen3.5-9b-Q4_K_M" therefore surfaces whoever wrote "Q4_K_M" in a
repo name (mostly modified fine-tunes), while the plain conversions that
everybody actually uses (quantization is normally a *file* in the repo, not
part of its name) rank far below. So:

  1. The query is understood first: quantization and size are pulled out of
     the text. The quantization is NOT sent to Hugging Face (it is a file
     property); it only boosts repos that carry it and later marks the
     matching file.
  2. Every hit is scored from signals a person would weigh by hand:
     name match, how many extra words the name has (extra words usually mean a
     modified version), who published it, downloads, likes and the
     publisher's followers.
  3. The score is split into named parts, so the UI can say *why* something
     ranks where it does. Nothing here is a hard filter: "purpose" and the
     like only add points.

Nothing is specific to one model or one agent. The publisher list below is
data, not logic: add a name and it counts, and an unknown publisher is simply
unranked, never penalised or hidden.
"""

from __future__ import annotations

import difflib
import math
import re
from typing import Optional

# ---------------------------------------------------------------------
# Data: publishers people already trust, and the vocabulary used only to
# repair typos. Both are plain lists on purpose.
# ---------------------------------------------------------------------

# Teams that published the original model.
CREATOR_ORGS = frozenset(
    {
        "qwen", "meta-llama", "google", "mistralai", "microsoft", "deepseek-ai", "nousresearch",
        "nvidia", "ibm-granite", "huggingfacetb", "openai", "allenai", "coherelabs", "01-ai",
        "zai-org", "moonshotai", "tiiuae", "stabilityai", "ai21labs", "internlm", "baichuan-inc",
    }
)

# Publishers that convert other people's models to GGUF, widely used.
QUANTIZER_ORGS = frozenset(
    {
        "lmstudio-community", "bartowski", "unsloth", "ggml-org", "mradermacher", "quantfactory",
        "maziyarpanahi", "thebloke", "second-state", "lmstudio-ai",
    }
)

FAMILY_VOCAB = (
    "qwen", "llama", "mistral", "mixtral", "gemma", "phi", "deepseek", "hermes", "yi", "granite",
    "smollm", "falcon", "command", "olmo", "glm", "kimi", "nemotron", "codestral", "devstral",
    "starcoder", "internlm", "mamba", "solar", "openchat", "zephyr", "vicuna", "orca", "pixtral",
    "llava", "minicpm", "baichuan", "stablelm", "tinyllama", "exaone", "gpt-oss", "magistral",
)

# Words in a repo name that do not make it a different model.
NOISE_WORDS = frozenset(
    {
        "gguf", "ggml", "instruct", "it", "chat", "imatrix", "i1", "quant", "quants", "quantized",
        "quantised", "hf", "model", "models", "base", "v1", "gptq", "awq", "mlx", "bnb",
    }
)

PURPOSES = ("any", "chat", "coding", "vision", "reasoning")
_PURPOSE_WORDS = {
    "chat": {"instruct", "chat", "it"},
    "coding": {"coder", "code", "codestral", "devstral", "starcoder", "coding"},
    "vision": {"vl", "vision", "llava", "vlm", "pixtral", "minicpm-v", "visual"},
    "reasoning": {"think", "thinking", "reason", "reasoning", "r1", "qwq", "cot"},
}

# Score budget (sums to 100; penalties come off the total).
W_RELEVANCE = 40
W_TRUST_TIER = {"creator": 18, "quantizer": 16}
W_TRUST_FOLLOWERS = 10
W_TRUST_CAP = 25
W_DOWNLOADS = 17
W_LIKES = 8
W_QUANT_IN_NAME = 5
W_PURPOSE = 12
PENALTY_PER_EXTRA_WORD = 5
PENALTY_EXTRA_CAP = 20
PENALTY_GATED = 10

# ---------------------------------------------------------------------
# Query understanding
# ---------------------------------------------------------------------

_QUANT_QUERY_RE = re.compile(
    r"(?<![a-z0-9])("
    r"iq\d(?:[_-]?[a-z]+)?(?:[_-]?[0-9a-z]+)?"
    r"|q\d(?:[_-]?[01](?![a-z0-9])|(?:[_-]?k)?(?:[_-]?[sml])?)"
    r"|bf16|fp16|f16|f32|mxfp4"
    r")(?![a-z0-9])",
    re.IGNORECASE,
)
_SIZE_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s?[Bb](?![A-Za-z])")


def canonical_quant(raw: str) -> str:
    """'q4km' / 'q4_k_m' / 'Q4-K-M' -> 'Q4_K_M'; 'q8' stays the prefix 'Q8'."""
    s = raw.lower().replace("-", "_")
    if s in ("bf16", "f16", "fp16", "f32", "mxfp4"):
        return {"fp16": "F16"}.get(s, s.upper())
    if s.startswith("iq"):
        parts = [p for p in re.split(r"_", re.sub(r"^(iq\d)", r"\1_", s)) if p]
        return "_".join(p.upper() for p in parts)
    m = re.match(r"^q(\d)_?([01])$", s)
    if m:
        return f"Q{m.group(1)}_{m.group(2)}"
    m = re.match(r"^q(\d)(?:_?(k))?(?:_?([sml]))?$", s)
    if not m:
        return raw.upper()
    out = f"Q{m.group(1)}"
    if m.group(2):
        out += "_K"
    if m.group(3):
        out += f"_{m.group(3).upper()}"
    return out


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9.]+", text.lower())


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def parse_query(text: str) -> dict:
    """What the person meant: the model words, a size, a quantization."""
    raw = (text or "").strip()
    quant = None
    m = _QUANT_QUERY_RE.search(raw)
    remainder = raw
    if m:
        quant = canonical_quant(m.group(1))
        remainder = (raw[: m.start()] + " " + raw[m.end():]).strip()
    tokens = [t for t in _tokens(remainder) if t not in ("gguf",)]
    size_m = _SIZE_RE.search(remainder)
    size_b = float(size_m.group(1)) if size_m else None
    return {
        "raw": raw,
        "tokens": tokens,
        "text": " ".join(tokens),
        "size_b": size_b,
        "quant": quant,
        "quant_is_prefix": bool(quant) and quant.count("_") == 0 and quant.startswith("Q"),
        "corrected_from": None,
    }


def correct_tokens(tokens: list[str], vocab=FAMILY_VOCAB) -> tuple[list[str], bool]:
    """Repair a mistyped model family ('qwwn3.5' -> 'qwen3.5'). Only the
    leading letters of a token are matched, and only against known family
    names, so ordinary words are never "corrected" into something else."""
    out, changed = [], False
    for t in tokens:
        m = re.match(r"^([a-z]{3,})(.*)$", t)
        if m and m.group(1) not in vocab:
            near = difflib.get_close_matches(m.group(1), vocab, n=1, cutoff=0.74)
            if near:
                out.append(near[0] + m.group(2))
                changed = True
                continue
        out.append(t)
    return out, changed


# ---------------------------------------------------------------------
# Reading a repo name
# ---------------------------------------------------------------------

def name_info(repo_id: str) -> dict:
    """Quantizations written in the name, and the name's own words."""
    name = repo_id.split("/", 1)[-1]
    quants = [canonical_quant(m.group(1)) for m in _QUANT_QUERY_RE.finditer(name.lower())]
    bare = _QUANT_QUERY_RE.sub(" ", name.lower())
    words = [w.strip(".") for w in re.split(r"[^a-z0-9.]+", bare) if w.strip(".")]
    return {"quants": quants, "words": words}


def _covered(word: str, query_squash: str, query_tokens: list[str]) -> bool:
    w = _squash(word)
    if not w:
        return True
    return w in query_squash or any(_squash(t) in w for t in query_tokens if _squash(t))


def extra_words(repo_id: str, query_tokens: list[str]) -> list[str]:
    """Words in the name that neither the search nor the usual conversion
    boilerplate explains: 'uncensored', 'heretic', 'v2-aggressive'... A name
    with several of these is a modified version, not the model itself."""
    qs = _squash("".join(query_tokens))
    out = []
    for w in name_info(repo_id)["words"]:
        if w in NOISE_WORDS or _SIZE_RE.fullmatch(w.upper()) or re.fullmatch(r"\d+(\.\d+)?", w):
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?b", w) or re.fullmatch(r"a\d+(?:\.\d+)?b", w) or re.fullmatch(r"v\d+(?:\.\d+)*", w):
            continue  # sizes, MoE active-size tags (A3B), plain version numbers
        if not _covered(w, qs, query_tokens):
            out.append(w)
    return out


def group_key(repo_id: str) -> str:
    """Repos that are the same model from different publishers share a key."""
    words = [w for w in name_info(repo_id)["words"] if w not in NOISE_WORDS]
    return _squash("".join(words))


def coverage(repo_id: str, query_tokens: list[str]) -> float:
    if not query_tokens:
        return 0.0
    hay = _squash(repo_id)
    hits = sum(1 for t in query_tokens if _squash(t) and _squash(t) in hay)
    return hits / len(query_tokens)


# ---------------------------------------------------------------------
# Trust
# ---------------------------------------------------------------------

def publisher_tier(author: str) -> str:
    a = author.lower()
    if a in CREATOR_ORGS:
        return "creator"
    if a in QUANTIZER_ORGS:
        return "quantizer"
    return "unknown"


_TIER_LABEL = {
    "creator": "Made by the model's own team",
    "quantizer": "Well-known GGUF publisher",
    "unknown": "Publisher not on the known list",
}


def _log_scale(value: float, full_at: float) -> float:
    if not value or value <= 0:
        return 0.0
    return min(1.0, math.log10(1 + value) / math.log10(1 + full_at))


def trust_info(author: str, followers: Optional[int]) -> dict:
    tier = publisher_tier(author)
    return {
        "tier": tier,
        "label": _TIER_LABEL[tier],
        "followers": followers,
    }


def _short(n: float) -> str:
    n = float(n)
    for div, suffix in ((1_000_000, "M"), (1_000, "k")):
        if n >= div:
            v = n / div
            return f"{v:.1f}".rstrip("0").rstrip(".") + suffix
    return str(int(n))


# ---------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------

def purpose_match(name_words: list[str], purpose: Optional[str]) -> bool:
    if not purpose or purpose == "any":
        return False
    wanted = _PURPOSE_WORDS.get(purpose, set())
    return any(w in wanted for w in name_words)


def score_repo(item: dict, parsed: dict, followers: Optional[int] = None, purpose: Optional[str] = None) -> dict:
    """`item` is one search hit (repo_id, downloads, likes, gated...).
    Returns the item plus score, score_parts, why, trust, role, group_key."""
    repo_id = item["repo_id"]
    author = repo_id.split("/", 1)[0]
    tokens = parsed["tokens"]
    info = name_info(repo_id)

    rel = coverage(repo_id, tokens)
    extras = extra_words(repo_id, tokens)
    tier = publisher_tier(author)

    parts = {
        "name_match": round(W_RELEVANCE * rel, 1),
        "modified_penalty": -min(PENALTY_EXTRA_CAP, PENALTY_PER_EXTRA_WORD * len(extras)),
        "publisher": W_TRUST_TIER.get(tier, 0),
        "followers": round(W_TRUST_FOLLOWERS * _log_scale(followers or 0, 10_000), 1) if followers else 0,
        "downloads": round(W_DOWNLOADS * _log_scale(item.get("downloads") or 0, 1_000_000), 1),
        "likes": round(W_LIKES * _log_scale(item.get("likes") or 0, 3_000), 1),
        "quant_in_name": W_QUANT_IN_NAME if parsed.get("quant") and parsed["quant"] in info["quants"] else 0,
        "purpose": W_PURPOSE if purpose_match(info["words"], purpose) else 0,
        "gated_penalty": -PENALTY_GATED if item.get("gated") else 0,
    }
    # publisher + followers share one budget so a big follower count can't
    # stack on top of a tier to outweigh the name match itself
    trust_total = min(W_TRUST_CAP, parts["publisher"] + parts["followers"])
    total = (
        parts["name_match"] + parts["modified_penalty"] + trust_total + parts["downloads"] + parts["likes"]
        + parts["quant_in_name"] + parts["purpose"] + parts["gated_penalty"]
    )

    why: list[str] = []
    if rel >= 0.999 and not extras:
        why.append("Same name as your search")
    elif rel >= 0.999:
        why.append(f"Matches your search, plus {len(extras)} extra word{'s' if len(extras) != 1 else ''} "
                   f"in the name ({', '.join(extras[:3])}{', …' if len(extras) > 3 else ''}) — usually a modified version")
    elif rel > 0:
        why.append(f"Matches {round(rel * 100)}% of your words")
    if tier != "unknown":
        why.append(_TIER_LABEL[tier])
    if followers:
        why.append(f"{_short(followers)} followers")
    if (item.get("downloads") or 0) > 0:
        why.append(f"{_short(item['downloads'])} downloads")
    if item.get("likes"):
        why.append(f"{_short(item['likes'])} likes")
    if parsed.get("quant") and parsed["quant"] in info["quants"]:
        why.append(f"Name mentions {parsed['quant']}")
    if parts["purpose"]:
        why.append(f"Fits “{purpose}”")

    out = dict(item)
    out.update(
        {
            "score": round(max(total, 0.0), 1),
            "score_parts": {**parts, "trust_total": round(trust_total, 1)},
            "why": why,
            "trust": trust_info(author, followers),
            "role": "conversion" if not extras else "variant",
            "extra_words": extras,
            "group_key": group_key(repo_id),
            "params_b": params_billion(repo_id),
            "quants_in_name": info["quants"],
        }
    )
    return out


def params_billion(repo_id: str) -> Optional[float]:
    m = _SIZE_RE.search(repo_id.split("/", 1)[-1])
    return float(m.group(1)) if m else None


def rank(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda r: (-r["score"], r["repo_id"]))


# ---------------------------------------------------------------------
# A rough "will this size fit" hint from the name alone
# ---------------------------------------------------------------------

Q4_BYTES_PER_PARAM = 0.6  # a Q4_K_M file is roughly 0.55-0.65 bytes per parameter


def fit_hint(params_b: Optional[float], hardware: Optional[dict]) -> Optional[dict]:
    """Typical Q4 size for a name that states its parameter count, judged by the
    same fit rules the per-file badges use. An estimate from the NAME, so the
    UI labels it as one; the real verdict is per file once it is opened."""
    if not params_b or not hardware:
        return None
    from . import hardware_fit

    size_bytes = params_b * 1e9 * Q4_BYTES_PER_PARAM
    est = hardware_fit.estimate_memory_mb(size_bytes)["total_mb"]
    verdict = hardware_fit.assess_from_snapshot(est, hardware)
    return {"approx_q4_bytes": int(size_bytes), "label": verdict.get("label"), "target": verdict.get("target")}
