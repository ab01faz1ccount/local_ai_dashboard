"""
scripts/capture_search_case.py

Records what Hugging Face / GitHub REALLY return for a query as a golden case,
so the ranking can be checked against real data (the bundled cases are
hand-written approximations). Run on your own machine, from the project folder:

    python scripts/capture_search_case.py model "qwen3.5-9B-Q6KM" --out my_cases.json
    python scripts/capture_search_case.py agent "hermes"          --out my_cases.json

It only reads public search results (no keys, nothing personal) and appends one
case to the file. Then open the file and fill in two fields by hand -- that is
the whole point of a golden case:

    "good":      ["owner/name", ...]   which result(s) should come first
    "must_beat": [["a", "b"], ...]     optional: a must rank above b

and check with:   python scripts/eval_search.py my_cases.json

GitHub allows about 10 unauthenticated searches a minute; one agent capture uses up to 3.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core import agent_discovery as ad  # noqa: E402
from backend.core import model_discovery as md  # noqa: E402
from backend.core import model_search as ms  # noqa: E402


def capture_model(query: str) -> dict:
    parsed = ms.parse_query(query)
    pool = md._hf_hits(parsed["tokens"], md.SEARCH_LIMIT)
    authors = list(dict.fromkeys(r["author"] for r in pool.values()))
    followers = {a: n for a, n in md._followers_for(authors).items() if n}
    keep = ("repo_id", "downloads", "likes", "gated")
    return {
        "id": "", "kind": "model", "query": query, "followers": followers,
        "candidates": [{k: r[k] for k in keep} for r in pool.values()],
        "good": [], "must_beat": [], "note": "captured live",
    }


def capture_agent(query: str) -> dict:
    pool, known = ad.collect_agent_pool(query.strip())
    keep = ("full_name", "description", "stars", "topics", "last_push", "archived", "license", "owner_type")
    return {
        "id": "", "kind": "agent", "query": query, "known": sorted(known),
        "candidates": [{k: r.get(k) for k in keep} for r in pool.values()],
        "good": [], "must_beat": [], "note": "captured live",
    }


def main(argv):
    if len(argv) < 2 or argv[0] not in ("model", "agent"):
        print(__doc__)
        return 2
    kind, query = argv[0], argv[1]
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None
    case = (capture_model if kind == "model" else capture_agent)(query)
    case["id"] = f"{kind}-" + "".join(c if c.isalnum() else "-" for c in query.lower()).strip("-")
    if out is None:
        print(json.dumps(case, indent=1, ensure_ascii=False))
        return 0
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"cases": []}
    data["cases"] = [c for c in data["cases"] if c["id"] != case["id"]] + [case]
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"saved '{case['id']}' ({len(case['candidates'])} candidates) -> {out}. Now fill in \"good\".")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
