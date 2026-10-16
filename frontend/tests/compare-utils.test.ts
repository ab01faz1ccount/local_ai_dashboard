import { describe, expect, it } from "vitest";
import { bitsText, formatContext, headsText, kvCacheText, sharedWebRepo, tensorMixText, valuesDiffer } from "../src/compare-utils";

describe("valuesDiffer", () => {
  it("is false when everything shown is the same, true when anything differs", () => {
    expect(valuesDiffer(["a", "a"])).toBe(false);
    expect(valuesDiffer(["a", "b"])).toBe(true);
    expect(valuesDiffer(["a", "a", "b"])).toBe(true);
  });
  it("treats missing values as one shared dash, so blank-vs-blank isn't a difference but blank-vs-value is", () => {
    expect(valuesDiffer([null, undefined, ""])).toBe(false);
    expect(valuesDiffer(["—", null])).toBe(false);
    expect(valuesDiffer([null, "9B"])).toBe(true);
  });
  it("one model has nothing to differ from", () => {
    expect(valuesDiffer(["a"])).toBe(false);
    expect(valuesDiffer([])).toBe(false);
  });
});

describe("tensorMixText", () => {
  it("shows the biggest shares, rounded, and tiny ones as <1%", () => {
    expect(tensorMixText([{ type: "Q4_K", share: 0.834, tensors: 100 }, { type: "Q6_K", share: 0.16, tensors: 10 }, { type: "F32", share: 0.004, tensors: 50 }])).toBe("Q4_K 83% · Q6_K 16% · F32 <1%");
  });
  it("limits how many it shows and survives empty input", () => {
    const mix = ["A", "B", "C", "D"].map((type) => ({ type, share: 0.25, tensors: 1 }));
    expect(tensorMixText(mix).split(" · ")).toHaveLength(3);
    expect(tensorMixText(mix, 4).split(" · ")).toHaveLength(4);
    expect(tensorMixText([])).toBe("—");
    expect(tensorMixText(undefined)).toBe("—");
  });
});

describe("number formatting", () => {
  it("context with thousands separators, missing as a dash", () => {
    expect(formatContext(32768)).toBe("32,768");
    expect(formatContext(null)).toBe("—");
    expect(formatContext(undefined)).toBe("—");
  });
  it("bits per weight", () => {
    expect(bitsText(4.8049)).toBe("4.80 bits");
    expect(bitsText(null)).toBe("—");
  });
  it("heads mention the cache heads only when they differ", () => {
    expect(headsText({ attention_heads: 32, kv_heads: 8 })).toBe("32 (8 for cache)");
    expect(headsText({ attention_heads: 8, kv_heads: 8 })).toBe("8");
    expect(headsText({ attention_heads: 8, kv_heads: null })).toBe("8");
    expect(headsText({ attention_heads: null, kv_heads: 2 })).toBe("—");
    expect(headsText(null)).toBe("—");
  });
});

describe("kvCacheText", () => {
  const cache = { bytes_per_token: 131072, layers_with_cache: 36, kv_heads: 8, head_dim: 128, note: null };
  it("converts bytes per token to MB or GB for a token count", () => {
    expect(kvCacheText(cache, 1000)).toBe("125 MB");
    expect(kvCacheText(cache, 32768)).toBe("4.00 GB");
    expect(kvCacheText({ ...cache, bytes_per_token: 1024 }, 1000)).toBe("1.0 MB");
    expect(kvCacheText({ ...cache, bytes_per_token: 65536 }, 32768)).toBe("2.00 GB"); // 2048 MB switches to GB
    expect(kvCacheText({ ...cache, bytes_per_token: 1024 }, 1024 * 1000)).toBe("1000 MB"); // just under 1 GB stays MB
  });
  it("no cache figure is a dash, never a guess", () => {
    expect(kvCacheText(null)).toBe("—");
    expect(kvCacheText(undefined)).toBe("—");
  });
});

describe("sharedWebRepo", () => {
  const row = (repo: string | null, withProfile = true) =>
    ({ knowledge: { status: "linked", repo, profile: withProfile ? { id: repo } : null, error: null } }) as never;
  it("names the repository when two models share it (case-insensitively)", () => {
    expect(sharedWebRepo([row("unsloth/Qwen3.5-9B-GGUF"), row("Unsloth/qwen3.5-9b-gguf")])).toBe("unsloth/Qwen3.5-9B-GGUF");
  });
  it("is null when every model came from a different repository", () => {
    expect(sharedWebRepo([row("a/x"), row("b/y")])).toBeNull();
  });
  it("ignores rows without a usable profile", () => {
    expect(sharedWebRepo([row("a/x"), row("a/x", false)])).toBeNull();
    expect(sharedWebRepo([row(null), row(null)])).toBeNull();
    expect(sharedWebRepo([{ knowledge: undefined } as never, row("a/x")])).toBeNull();
  });
  it("still finds the shared repo when a third model is different", () => {
    expect(sharedWebRepo([row("a/x"), row("b/y"), row("a/x")])).toBe("a/x");
  });
});
