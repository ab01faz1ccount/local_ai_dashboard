"""
backend/core/knowledge.py

Sourced facts about a model or an agent, for the comparison tables.

Every fact carries WHERE it came from (a label and a link the user can open)
and HOW it was obtained:

  source      the model card / repository itself says it
  base_model  not on this card, taken from the model it was made from (named)
  derived     worked out by the app from the file name (a guess, labelled)
  missing     the source does not say -- shown as "not published", never blank

Only reputable primary sources are used, through the same host allow-list as
the rest of online discovery: Hugging Face for models, GitHub for agents.
Nothing is invented: a fact the source does not state is `missing`.

The parsers are pure (a dict or a README string in, facts out) so every shape
the APIs can take is testable offline; only `fetch_*` touch the network.
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional
from urllib.parse import quote

from . import model_discovery
from .net_util import DiscoveryError, get_json, get_text

HF_API = "https://huggingface.co/api/models/{repo}"
HF_PAGE = "https://huggingface.co/{repo}"
HF_README = "https://huggingface.co/{repo}/raw/main/README.md"
GITHUB_API = "https://api.github.com"
GITHUB_HOSTS = ("github.com", "githubusercontent.com")

CACHE_SECONDS = 3600
_cache: dict[str, tuple[float, dict]] = {}

SOURCE, BASE_MODEL, DERIVED, MISSING = "source", "base_model", "derived", "missing"


def _source(label: str, url: str) -> dict:
    return {"label": label, "url": url}


def fact(key: str, label: str, value: Any, *, source: Optional[dict] = None, provenance: str = SOURCE, note: Optional[str] = None) -> dict:
    """One row of a profile. A value that is empty/None is always `missing`."""
    empty = value is None or value == "" or value == []
    if empty:
        return {"key": key, "label": label, "value": None, "provenance": MISSING, "source": source, "note": note or "Not published by the source."}
    return {"key": key, "label": label, "value": value, "provenance": provenance, "source": source, "note": note}


# ---------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
_HTML_RE = re.compile(r"<[^>]+>")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_BADGE_LINE_RE = re.compile(r"^\s*(\[!\[|!\[|<img|<a |<p |<div|</)", re.IGNORECASE)


def clean_inline(text: str) -> str:
    """Markdown/HTML decoration out of one line of text."""
    text = _IMAGE_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _HTML_RE.sub("", text)
    text = re.sub(r"[*_`]{1,3}", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:- ")
    return cut + "…"


def extract_summary(markdown: str, limit: int = 380) -> Optional[str]:
    """The first real paragraph of a README / model card: skips front matter,
    badges, images, headings, code and tables. None when there is none."""
    if not markdown:
        return None
    text = _FRONT_MATTER_RE.sub("", markdown, count=1)
    text = _FENCE_RE.sub("", text)
    paragraphs, current = [], []
    for raw in text.splitlines() + [""]:
        line = raw.strip()
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if line.startswith(("#", "|", ">", "---", "===")) or _BADGE_LINE_RE.match(line):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line)
    for p in paragraphs:
        cleaned = clean_inline(p)
        # a real sentence, not a nav row or a one-word tagline
        if len(cleaned) >= 50 and " " in cleaned:
            return _truncate(cleaned, limit)
    return None


_FEATURE_HEADING_RE = re.compile(
    r"^(#{1,4})\s*(?:[^\w\s]\s*)?(key\s+)?(features?|capabilit(?:y|ies)|what\s+(?:it\s+)?(?:can|does)|highlights?|why\b.*|"
    r"what\s+you\s+can\s+do|abilities)\b.*$",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")


def extract_capabilities(markdown: str, limit: int = 8, width: int = 140) -> list[str]:
    """Bullets under a 'Features' / 'Capabilities' style heading. [] when the
    README has no such section (the caller then says so instead of guessing)."""
    if not markdown:
        return []
    text = _FENCE_RE.sub("", _FRONT_MATTER_RE.sub("", markdown, count=1))
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines) and len(out) < limit:
        m = _FEATURE_HEADING_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        level = len(m.group(1))
        i += 1
        while i < len(lines) and len(out) < limit:
            line = lines[i]
            h = re.match(r"^(#{1,6})\s", line.strip())
            if h and len(h.group(1)) <= level:
                break
            b = _BULLET_RE.match(line)
            if b:
                item = clean_inline(b.group(1))
                if len(item) >= 8:
                    out.append(_truncate(item, width))
            i += 1
    return out


def _fmt_count(n: Optional[float]) -> Optional[str]:
    if n is None:
        return None
    n = float(n)
    if n >= 1e12:
        return f"{n / 1e12:.1f}T"
    if n >= 1e9:
        return f"{n / 1e9:.1f}B"
    if n >= 1e6:
        return f"{n / 1e6:.0f}M"
    return f"{n:.0f}"


# ---------------------------------------------------------------------
# models (Hugging Face)
# ---------------------------------------------------------------------

def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _tag_values(tags: list, prefix: str) -> list[str]:
    return [t.split(":", 1)[1] for t in tags if isinstance(t, str) and t.startswith(prefix + ":")]


def parse_model_info(info: dict) -> dict:
    """Raw fields out of the Hugging Face model API response."""
    card = info.get("cardData") or {}
    tags = [t for t in (info.get("tags") or []) if isinstance(t, str)]
    gguf = info.get("gguf") or {}
    safetensors = (info.get("safetensors") or {}).get("total")

    license_ = card.get("license")
    if not license_:
        lic = _tag_values(tags, "license")
        license_ = lic[0] if lic else None

    languages = [str(x) for x in _as_list(card.get("language"))] or _tag_values(tags, "language")

    base = [b for b in (str(x) for x in _as_list(card.get("base_model"))) if re.match(r"^[\w.-]+/[\w.-]+$", b)]
    if not base:
        # tags look like "base_model:Qwen/Qwen2.5-7B-Instruct" or "base_model:quantized:Qwen/..."
        for t in _tag_values(tags, "base_model"):
            cand = t.split(":")[-1]
            if re.match(r"^[\w.-]+/[\w.-]+$", cand):
                base.append(cand)

    params = gguf.get("total") or safetensors
    evals = []
    index = card.get("model-index")
    if isinstance(index, list):
        for entry in index:
            for res in (entry or {}).get("results", []) or []:
                dataset = (res.get("dataset") or {}).get("name")
                for metric in res.get("metrics", []) or []:
                    value = metric.get("value")
                    if value is None:
                        continue
                    evals.append(
                        {
                            "dataset": dataset or (res.get("task") or {}).get("type") or "eval",
                            "metric": metric.get("name") or metric.get("type") or "score",
                            "value": value,
                        }
                    )
    return {
        "license": license_,
        "languages": languages,
        "base_models": base,
        "architecture": gguf.get("architecture"),
        "context_length": gguf.get("context_length"),
        "parameters": params,
        "pipeline": info.get("pipeline_tag"),
        "downloads": info.get("downloads"),
        "likes": info.get("likes"),
        "updated": info.get("lastModified"),
        "evals": evals,
    }


def build_model_profile(
    repo_id: str,
    info: dict,
    readme: Optional[str],
    base: Optional[tuple[str, dict, Optional[str]]] = None,
    filename: Optional[str] = None,
) -> dict:
    """`base` = (base_repo_id, its API info, its README) when the card names the
    model it was made from -- used ONLY to fill what this card leaves out."""
    me = parse_model_info(info)
    src = _source("Hugging Face model card", HF_PAGE.format(repo=repo_id))
    bme = parse_model_info(base[1]) if base else None
    bsrc = _source(f"Base model card: {base[0]}", HF_PAGE.format(repo=base[0])) if base else None

    def pick(key: str, label: str, mine: Any, theirs: Any = None, fmt=lambda v: v, derived: Any = None, derived_note: Optional[str] = None):
        if mine not in (None, "", []):
            return fact(key, label, fmt(mine), source=src)
        if bme is not None and theirs not in (None, "", []):
            return fact(key, label, fmt(theirs), source=bsrc, provenance=BASE_MODEL, note=f"Not on this card; from the base model {base[0]}.")
        if derived not in (None, ""):
            return fact(key, label, derived, provenance=DERIVED, note=derived_note)
        return fact(key, label, None, source=src, note="The model card does not state this.")

    name_for_guess = filename or repo_id
    params_guess = model_discovery.guess_param_count(repo_id)
    quant_guess = model_discovery.guess_quantization(filename) if filename else None

    summary = extract_summary(readme or "")
    if summary:
        description = fact("description", "About", summary, source=src)
    elif base and extract_summary(base[2] or ""):
        description = fact("description", "About", extract_summary(base[2] or ""), source=bsrc, provenance=BASE_MODEL,
                           note=f"This card has no description; from the base model {base[0]}.")
    else:
        description = fact("description", "About", None, source=src, note="The model card has no description.")

    facts = [
        description,
        pick("license", "License", me["license"], bme and bme["license"]),
        fact(
            "base_model", "Made from", ", ".join(me["base_models"]) if me["base_models"] else None, source=src,
            note="No base model is declared (an original model, or the card does not say).",
        ),
        pick("architecture", "Architecture", me["architecture"], bme and bme["architecture"]),
        pick("parameters", "Parameters", me["parameters"], bme and bme["parameters"], fmt=_fmt_count,
             derived=params_guess, derived_note="Guessed from the name; the card does not state it."),
        pick("context_length", "Context window", me["context_length"], bme and bme["context_length"],
             fmt=lambda v: f"{int(v):,} tokens"),
        pick("languages", "Languages", me["languages"], bme and bme["languages"], fmt=lambda v: ", ".join(v[:12]) + (" …" if len(v) > 12 else "")),
        pick("pipeline", "Task", me["pipeline"], bme and bme["pipeline"]),
        fact("downloads", "Downloads", f"{int(me['downloads']):,}" if me["downloads"] is not None else None, source=src),
        fact("likes", "Likes", f"{int(me['likes']):,}" if me["likes"] is not None else None, source=src),
        fact("updated", "Last updated", (me["updated"] or "")[:10] or None, source=src),
    ]
    if quant_guess:
        facts.append(fact("quantization", "Quantization", quant_guess, provenance=DERIVED, note="Read from the file name."))

    evals, evals_src, evals_prov = me["evals"], src, SOURCE
    if not evals and bme and bme["evals"]:
        evals, evals_src, evals_prov = bme["evals"], bsrc, BASE_MODEL
    benchmarks = fact(
        "benchmarks", "Published benchmarks", evals[:12] if evals else None, source=evals_src, provenance=evals_prov,
        note=("Published by the model's authors, not measured here." if evals else "No benchmark results are published on the card."),
    )
    facts.append(benchmarks)

    return {
        "kind": "model",
        "id": repo_id,
        "url": HF_PAGE.format(repo=repo_id),
        "facts": facts,
        "sources": [s for s in (src, bsrc) if s],
        "fetched_at": int(time.time()),
    }


_QUANT_IN_NAME_RE = re.compile(r"(?<![A-Za-z0-9])(IQ\d_[A-Z0-9]+|Q\d_K_[SML]|Q\d_K|Q\d_[01]|BF16|F16|F32|MXFP4)(?![A-Za-z0-9])", re.IGNORECASE)
_SHARD_IN_NAME_RE = re.compile(r"-\d{5}-of-\d{5}", re.IGNORECASE)


def clean_model_query(name: str) -> str:
    """A local file/model name as a search phrase: no extension, shard suffix or
    quantization tag ("Qwen2.5-7B-Instruct-Q4_K_M.gguf" -> "Qwen2.5 7B Instruct")."""
    text = re.sub(r"\.gguf$", "", name.strip(), flags=re.IGNORECASE)
    text = _SHARD_IN_NAME_RE.sub("", text)
    text = _QUANT_IN_NAME_RE.sub(" ", text)
    return re.sub(r"[\s_\-]+", " ", text).strip()


def find_model_repo(name: str) -> Optional[dict]:
    """The Hugging Face repo that best matches a local model's name, or None.
    Only a match that contains EVERY word of the cleaned name counts -- a
    near-miss would put another model's facts next to this one's name."""
    query = clean_model_query(name)
    if not query:
        return None
    found = model_discovery.search_models(query, limit=5)
    exact = [r for r in found if r["relevance"] >= 0.999 and not r.get("gated")]
    return exact[0] if exact else None


# the agents the app has an adapter for, whose home repository is known for certain
KNOWN_AGENT_REPOS = {"open_interpreter": "openinterpreter/openinterpreter"}


def _cached(key: str, loader) -> dict:
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = loader()
    _cache[key] = (now, value)
    return value


def fetch_model_profile(repo_id: str, filename: Optional[str] = None) -> dict:
    if not model_discovery.REPO_ID_RE.match(repo_id):
        raise DiscoveryError("اسم repo معتبر نیست (باید به شکل owner/name باشه).", "bad_input")

    def load() -> dict:
        info = get_json(HF_API.format(repo=quote(repo_id, safe="/")), allowed_hosts=model_discovery.HF_ALLOWED_HOSTS)
        try:
            readme = get_text(HF_README.format(repo=quote(repo_id, safe="/")), allowed_hosts=model_discovery.HF_ALLOWED_HOSTS)
        except DiscoveryError:
            readme = None
        base = None
        base_ids = parse_model_info(info)["base_models"]
        if base_ids and base_ids[0].lower() != repo_id.lower():
            try:
                b_info = get_json(HF_API.format(repo=quote(base_ids[0], safe="/")), allowed_hosts=model_discovery.HF_ALLOWED_HOSTS)
                try:
                    b_readme = get_text(HF_README.format(repo=quote(base_ids[0], safe="/")), allowed_hosts=model_discovery.HF_ALLOWED_HOSTS)
                except DiscoveryError:
                    b_readme = None
                base = (base_ids[0], b_info, b_readme)
            except DiscoveryError:
                base = None  # the base card is a bonus; its absence is not an error
        return build_model_profile(repo_id, info, readme, base, filename)

    return _cached(f"model:{repo_id}:{filename or ''}", load)


# ---------------------------------------------------------------------
# agents (GitHub)
# ---------------------------------------------------------------------

def build_agent_profile(full_name: str, info: dict, readme: Optional[str], release: Optional[dict] = None) -> dict:
    url = info.get("html_url") or f"https://github.com/{full_name}"
    src = _source("GitHub repository", url)
    lic = (info.get("license") or {}).get("spdx_id")
    if lic in ("NOASSERTION", "null"):
        lic = None

    caps = extract_capabilities(readme or "")
    if caps:
        capability = fact("capabilities", "What it can do", caps, source=_source("Repository README", url + "#readme"))
    else:
        topics = [t for t in (info.get("topics") or []) if isinstance(t, str)]
        capability = fact(
            "capabilities", "What it can do", None, source=_source("Repository README", url + "#readme"),
            note="The README has no features list." + (f" Topics: {', '.join(topics[:8])}." if topics else ""),
        )

    desc = info.get("description") or extract_summary(readme or "")
    facts = [
        fact("description", "About", clean_inline(desc) if desc else None, source=src),
        capability,
        fact("license", "License", lic, source=src, note="The repository does not declare a license." if not lic else None),
        fact("language", "Written in", info.get("language"), source=src),
        fact("stars", "Stars", f"{int(info['stargazers_count']):,}" if info.get("stargazers_count") is not None else None, source=src),
        fact("forks", "Forks", f"{int(info['forks_count']):,}" if info.get("forks_count") is not None else None, source=src),
        fact("pushed", "Last activity", (info.get("pushed_at") or "")[:10] or None, source=src),
        fact(
            "release", "Latest release",
            (f"{release.get('tag_name')} ({(release.get('published_at') or '')[:10]})" if release and release.get("tag_name") else None),
            source=_source("GitHub releases", url + "/releases"),
            note="No published releases." if not release else None,
        ),
        fact("homepage", "Website", info.get("homepage") or None, source=src),
    ]
    if info.get("archived"):
        facts.append(fact("archived", "Status", "Archived — no longer maintained", source=src))
    return {
        "kind": "agent",
        "id": full_name,
        "url": url,
        "facts": facts,
        "sources": [src],
        "fetched_at": int(time.time()),
    }


def fetch_agent_profile(full_name: str) -> dict:
    from .agent_discovery import REPO_FULL_NAME_RE

    if not REPO_FULL_NAME_RE.match(full_name):
        raise DiscoveryError("اسم repo معتبر نیست (باید به شکل owner/name باشه).", "bad_input")

    def load() -> dict:
        base = f"{GITHUB_API}/repos/{quote(full_name, safe='/')}"
        info = get_json(base, allowed_hosts=GITHUB_HOSTS, headers={"Accept": "application/vnd.github+json"})
        try:
            readme = get_text(base + "/readme", allowed_hosts=GITHUB_HOSTS, headers={"Accept": "application/vnd.github.raw+json"})
        except DiscoveryError:
            readme = None
        try:
            release = get_json(base + "/releases/latest", allowed_hosts=GITHUB_HOSTS, headers={"Accept": "application/vnd.github+json"})
        except DiscoveryError:
            release = None  # most repos have none; that is a fact, not an error
        return build_agent_profile(full_name, info, readme, release)

    return _cached(f"agent:{full_name}", load)
