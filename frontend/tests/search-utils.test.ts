import { describe, it, expect } from "vitest";
import {
  applyFilters, sortResults, splitByRole, trustBadge, followersText, quantMatches, filesForQuery,
  inSizeBucket, activeFilterCount, huggingFaceSearchUrl, DEFAULT_FILTERS,
} from "../src/search-utils";
import type { HfModelResult, HfFileCandidate } from "../src/api";

const r = (repo: string, over: Partial<HfModelResult> = {}): HfModelResult => ({
  repo_id: repo, author: repo.split("/")[0], name: repo.split("/")[1], downloads: 10, likes: 1, updated: null,
  gated: false, url: "u", relevance: 1, ...over,
});
const f = (name: string, quant: string | null): HfFileCandidate =>
  ({ filename: name, display_name: name, quantization: quant, size_bytes: 1, parts: 1, verifiable: true, files: [] }) as HfFileCandidate;

describe("size buckets", () => {
  it("places sizes on the right side of each edge", () => {
    expect(inSizeBucket(0, "tiny")).toBe(true);
    expect(inSizeBucket(4, "tiny")).toBe(true);
    expect(inSizeBucket(4.5, "tiny")).toBe(false);
    expect(inSizeBucket(9, "small")).toBe(true);
    expect(inSizeBucket(14, "medium")).toBe(true);
    expect(inSizeBucket(35, "large")).toBe(true);
    expect(inSizeBucket(70, "huge")).toBe(true);
    expect(inSizeBucket(70, "large")).toBe(false);
  });
  it("never hides a repo whose name does not state a size", () => {
    expect(inSizeBucket(null, "small")).toBe(true);
    expect(inSizeBucket(undefined, "huge")).toBe(true);
  });
});

describe("applyFilters", () => {
  const list = [
    r("lmstudio-community/A-9B", { trust: { tier: "quantizer", label: "", followers: 5 }, params_b: 9 }),
    r("nobody/A-9B", { params_b: 9, gated: true }),
    r("qwen/A-70B", { trust: { tier: "creator", label: "", followers: null }, params_b: 70 }),
  ];
  it("keeps everything by default", () => expect(applyFilters(list, DEFAULT_FILTERS)).toHaveLength(3));
  it("trusted only drops unknown publishers, keeps known ones", () => {
    expect(applyFilters(list, { ...DEFAULT_FILTERS, trustedOnly: true }).map((x) => x.author)).toEqual(["lmstudio-community", "qwen"]);
  });
  it("hide gated and size combine", () => {
    expect(applyFilters(list, { trustedOnly: false, hideGated: true, size: "small" }).map((x) => x.author)).toEqual(["lmstudio-community"]);
  });
  it("counts the active filters", () => {
    expect(activeFilterCount(DEFAULT_FILTERS)).toBe(0);
    expect(activeFilterCount({ trustedOnly: true, hideGated: true, size: "tiny" })).toBe(3);
  });
});

describe("sortResults", () => {
  const list = [r("a/x", { downloads: 5, updated: "2026-01-01T00:00:00Z" }), r("b/x", { downloads: 50, updated: "2025-01-01T00:00:00Z" }), r("c/x", { downloads: 1, updated: null })];
  it("best keeps the backend ranking and does not mutate", () => {
    expect(sortResults(list, "best").map((x) => x.author)).toEqual(["a", "b", "c"]);
  });
  it("by downloads and by recency (unknown dates last)", () => {
    expect(sortResults(list, "downloads").map((x) => x.author)).toEqual(["b", "a", "c"]);
    expect(sortResults(list, "newest").map((x) => x.author)).toEqual(["a", "b", "c"]);
    expect(list.map((x) => x.author)).toEqual(["a", "b", "c"]);
  });
});

describe("splitByRole / trust", () => {
  it("separates modified versions; a missing role counts as the model itself", () => {
    const s = splitByRole([r("a/x"), r("b/x", { role: "variant" }), r("c/x", { role: "conversion" })]);
    expect(s.main.map((x) => x.author)).toEqual(["a", "c"]);
    expect(s.variants.map((x) => x.author)).toEqual(["b"]);
  });
  it("labels known publishers and says nothing about unknown ones", () => {
    expect(trustBadge(r("a/x", { trust: { tier: "creator", label: "", followers: null } }))?.text).toBe("Official");
    expect(trustBadge(r("a/x", { trust: { tier: "quantizer", label: "", followers: null } }))?.text).toBe("Known publisher");
    expect(trustBadge(r("a/x", { trust: { tier: "unknown", label: "", followers: null } }))).toBeNull();
    expect(trustBadge(r("a/x"))).toBeNull();
  });
  it("formats followers, and null means 'not published', not zero", () => {
    expect(followersText(null)).toBeNull();
    expect(followersText(undefined)).toBeNull();
    expect(followersText(0)).toBe("0 followers");
    expect(followersText(1)).toBe("1 follower");
    expect(followersText(4200)).toBe("4.2k followers");
    expect(followersText(12000)).toBe("12k followers");
    expect(followersText(2_500_000)).toBe("2.5M followers");
  });
});

describe("quantization matching", () => {
  it("exact and prefix", () => {
    expect(quantMatches("Q6_K_M", "Q6_K_M")).toBe(true);
    expect(quantMatches("q6_k_m", "Q6_K_M")).toBe(true);
    expect(quantMatches("Q6_K", "Q6_K_M")).toBe(false);
    expect(quantMatches("Q4_K_S", "Q4")).toBe(true);
    expect(quantMatches("Q40_X", "Q4")).toBe(false);
    expect(quantMatches(null, "Q4")).toBe(false);
    expect(quantMatches("Q4_K_M", null)).toBe(false);
  });
  it("puts matching files first and otherwise keeps the given order", () => {
    const files = [f("a", "Q8_0"), f("b", "Q4_K_M"), f("c", "Q6_K_M"), f("d", "Q6_K_M")];
    expect(filesForQuery(files, "Q6_K_M").map((x) => x.filename)).toEqual(["c", "d", "a", "b"]);
    expect(filesForQuery(files, null)).toBe(files);
  });
});

describe("links", () => {
  it("builds a Hugging Face search link for the GGUF library", () => {
    expect(huggingFaceSearchUrl("qwen 3.5 9b")).toBe("https://huggingface.co/models?library=gguf&sort=downloads&search=qwen%203.5%209b");
  });
});
