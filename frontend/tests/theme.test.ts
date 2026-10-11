import { describe, it, expect } from "vitest";
import {
  DEFAULT_PALETTE,
  PALETTES,
  PALETTE_STORAGE_KEY,
  applyPalette,
  loadPalette,
  normalizePalette,
  savePalette,
} from "../src/theme";

const memoryStorage = (initial: Record<string, string> = {}) => {
  const data = { ...initial };
  return {
    data,
    getItem: (k: string) => (k in data ? data[k] : null),
    setItem: (k: string, v: string) => {
      data[k] = v;
    },
  };
};

describe("normalizePalette", () => {
  it("keeps the two real palettes", () => {
    expect(normalizePalette("aqua")).toBe("aqua");
    expect(normalizePalette("mint")).toBe("mint");
  });
  it("anything else falls back to the default", () => {
    for (const v of [null, undefined, "", "red", 3, {}, "AQUA"]) expect(normalizePalette(v)).toBe(DEFAULT_PALETTE);
  });
  it("every listed palette is one normalize accepts", () => {
    for (const p of PALETTES) expect(normalizePalette(p.id)).toBe(p.id);
  });
});

describe("load / save", () => {
  it("round-trips the choice through storage", () => {
    const s = memoryStorage();
    savePalette("mint", s);
    expect(s.data[PALETTE_STORAGE_KEY]).toBe("mint");
    expect(loadPalette(s)).toBe("mint");
  });
  it("a stale or corrupt stored value gives the default", () => {
    expect(loadPalette(memoryStorage({ [PALETTE_STORAGE_KEY]: "purple" }))).toBe(DEFAULT_PALETTE);
  });
  it("no storage at all still works", () => {
    expect(loadPalette(null)).toBe(DEFAULT_PALETTE);
    expect(() => savePalette("mint", null)).not.toThrow();
  });
  it("storage that throws (blocked site data) never breaks the app", () => {
    const broken = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
    };
    expect(loadPalette(broken)).toBe(DEFAULT_PALETTE);
    expect(() => savePalette("mint", broken)).not.toThrow();
  });
});

describe("applyPalette", () => {
  it("sets data-palette on the root element", () => {
    const el = document.createElement("div");
    applyPalette("mint", el);
    expect(el.getAttribute("data-palette")).toBe("mint");
    applyPalette("aqua", el);
    expect(el.getAttribute("data-palette")).toBe("aqua");
  });
});
