"""
scripts/eval_search.py

Runs the golden search cases (tests/fixtures/search_golden.json, or a file you
pass) through the real ranking code and prints where the right answers land.
Needs only Python; no network.

    python scripts/eval_search.py                 # summary + every failing case
    python scripts/eval_search.py -v              # every case with its ranking
    python scripts/eval_search.py my_cases.json
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# the scorers are pure; load them without importing the whole backend
for name, sub in (("backend", ""), ("backend.core", "/core")):
    m = types.ModuleType(name)
    m.__path__ = [str(ROOT / "backend") + sub]
    sys.modules.setdefault(name, m)
from backend.core import search_eval  # noqa: E402


def main(argv):
    verbose = "-v" in argv
    paths = [a for a in argv if not a.startswith("-")]
    path = paths[0] if paths else ROOT / "tests" / "fixtures" / "search_golden.json"
    results, summary = search_eval.evaluate_file(path)
    for r in results:
        if r.passed and not verbose:
            continue
        print(f"{'PASS' if r.passed else 'FAIL'}  {r.id}   \"{r.query}\"")
        if r.problem:
            print(f"      {r.problem}")
        if verbose or not r.passed:
            for i, name in enumerate(r.ranking, 1):
                print(f"      {i:>2}. {name}")
    print(f"\n{summary['passed']}/{summary['cases']} cases pass   hit@1={summary['hit_at_1']}   MRR={summary['mrr']}")
    return 0 if summary["passed"] == summary["cases"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
