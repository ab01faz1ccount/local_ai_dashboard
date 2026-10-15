"""
scripts/probe_hf_search.py

Read-only check of what Hugging Face actually returns for the search the app
now runs. Needs only Python; changes nothing. Run from the project folder:

    python scripts/probe_hf_search.py "qwen3.5-9B-Q6KM"

It prints (1) how the app understood the query, (2) the ranked results with
the reasons, and (3) the raw follower fields Hugging Face gave for each
publisher -- so we can see which endpoints/fields are real before trusting
them. Copy the output back (it has no keys or personal data).
"""
import json
import sys
import urllib.request

sys.path.insert(0, ".")
from backend.core import model_discovery as md  # noqa: E402
from backend.core import model_search as ms  # noqa: E402


def raw(url):
    req = urllib.request.Request(url, headers={"User-Agent": "local-ai-control-center-probe"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def main():
    query = " ".join(sys.argv[1:]) or "qwen3.5-9B-Q6KM"
    print("== understood ==")
    print(json.dumps(ms.parse_query(query), indent=2))
    out = md.search_models_full(query)
    print("\n== query ==\n", json.dumps(out["query"], indent=2))
    print("\n== ranked ==")
    for i, r in enumerate(out["results"], 1):
        print(f"{i:>2}. {r['score']:>5}  {r['role']:<10} {r['repo_id']}  dl={r['downloads']} likes={r['likes']} "
              f"followers={r['trust']['followers']}")
        print("      ", " | ".join(r["why"]))
    print("\n== raw follower fields ==")
    for a in list(dict.fromkeys(r["author"] for r in out["results"]))[:6]:
        for kind in ("organizations", "users"):
            status, body = raw(f"https://huggingface.co/api/{kind}/{a}/overview")
            keys = sorted(body)[:12] if isinstance(body, dict) else body
            print(f"{a:<28} /{kind}/overview -> {status} {keys}")
            if isinstance(body, dict):
                print("      numFollowers =", body.get("numFollowers"), "| followerCount =", body.get("followerCount"))
                break


if __name__ == "__main__":
    main()
