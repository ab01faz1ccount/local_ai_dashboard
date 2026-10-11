import { describe, it, expect } from "vitest";
import { factKeysInOrder, factLines, findFact, isBenchmarkList, provenanceLabel, safeSourceUrl } from "../src/knowledge-utils";
import type { KnowledgeFact, KnowledgeProfile } from "../src/api";

const f = (key: string, value: KnowledgeFact["value"], provenance: KnowledgeFact["provenance"] = "source"): KnowledgeFact => ({
  key, label: key.toUpperCase(), value, provenance, source: null, note: null,
});
const profile = (...facts: KnowledgeFact[]): KnowledgeProfile => ({ kind: "model", id: "a/b", url: "https://huggingface.co/a/b", facts, sources: [], fetched_at: 0 });

describe("provenanceLabel", () => {
  it("says where a non-primary fact came from, and nothing for a primary one", () => {
    expect(provenanceLabel("source")).toBeNull();
    expect(provenanceLabel("base_model")).toBe("from the base model");
    expect(provenanceLabel("derived")).toBe("guessed from the name");
    expect(provenanceLabel("missing")).toBe("not published");
  });
});

describe("factLines", () => {
  it("text, list and benchmark values become lines; a missing value none", () => {
    expect(factLines(f("a", "Apache"))).toEqual(["Apache"]);
    expect(factLines(f("a", ["x", "y"]))).toEqual(["x", "y"]);
    expect(factLines(f("a", [{ dataset: "MMLU", metric: "accuracy", value: 74.2 }]))).toEqual(["MMLU · accuracy: 74.2"]);
    expect(factLines(f("a", null, "missing"))).toEqual([]);
  });
  it("isBenchmarkList tells results from plain lists", () => {
    expect(isBenchmarkList([{ dataset: "d", metric: "m", value: 1 }])).toBe(true);
    expect(isBenchmarkList(["a"])).toBe(false);
    expect(isBenchmarkList([])).toBe(false);
    expect(isBenchmarkList(null)).toBe(false);
  });
});

describe("factKeysInOrder", () => {
  it("is the union of keys, in the order the first profile having each lists them", () => {
    const a = profile(f("license", "x"), f("params", "7B"));
    const b = profile(f("license", "y"), f("context", "8k"), f("params", "3B"));
    expect(factKeysInOrder([a, b]).map((r) => r.key)).toEqual(["license", "params", "context"]);
  });
  it("skips columns that have no profile", () => {
    expect(factKeysInOrder([null, undefined, profile(f("a", "x"))]).map((r) => r.key)).toEqual(["a"]);
    expect(factKeysInOrder([])).toEqual([]);
  });
  it("findFact finds by key or says null", () => {
    expect(findFact(profile(f("a", "x")), "a")?.value).toBe("x");
    expect(findFact(profile(f("a", "x")), "z")).toBeNull();
    expect(findFact(null, "a")).toBeNull();
  });
});

describe("safeSourceUrl", () => {
  it("lets https links to Hugging Face and GitHub through", () => {
    expect(safeSourceUrl("https://huggingface.co/Qwen/Qwen2.5")).toBe("https://huggingface.co/Qwen/Qwen2.5");
    expect(safeSourceUrl("https://github.com/acme/agent#readme")).toBe("https://github.com/acme/agent#readme");
    expect(safeSourceUrl("https://www.github.com/x/y")).toBe("https://www.github.com/x/y");
  });
  it("refuses everything else", () => {
    for (const bad of ["javascript:alert(1)", "http://github.com/a/b", "https://evil.com/github.com", "https://github.com.evil.com/x", "data:text/html,x", "not a url", "", null, undefined]) {
      expect(safeSourceUrl(bad as string | null)).toBeNull();
    }
  });
});
