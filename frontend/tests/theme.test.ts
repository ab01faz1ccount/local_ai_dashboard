import { describe, it, expect } from "vitest";
import {
  DEFAULT_PALETTE,
  PALETTES,
  PALETTE_STORAGE_KEY,
  applyPalette,
  loadPalette,
  normalizePalette,
  savePalette,
  DEFAULT_MODE,
  MODES,
  MODE_STORAGE_KEY,
  applyMode,
  loadMode,
  normalizeMode,
  saveMode,
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


describe("mode (soft dark / soft light background)", () => {
  it("dark is the default and the two real modes are kept", () => {
    expect(DEFAULT_MODE).toBe("dark");
    expect(normalizeMode("light")).toBe("light");
    expect(normalizeMode("dark")).toBe("dark");
    for (const v of [null, undefined, "", "sepia", "LIGHT", 1, {}]) expect(normalizeMode(v)).toBe("dark");
    for (const m of MODES) expect(normalizeMode(m.id)).toBe(m.id);
    expect(MODES.map((m) => m.id)).toEqual(["light", "dark"]);
  });
  it("round-trips through storage, separately from the palette", () => {
    const s = memoryStorage();
    saveMode("light", s);
    savePalette("mint", s);
    expect(s.data[MODE_STORAGE_KEY]).toBe("light");
    expect(MODE_STORAGE_KEY).not.toBe(PALETTE_STORAGE_KEY);
    expect(loadMode(s)).toBe("light");
    expect(loadPalette(s)).toBe("mint");
  });
  it("corrupt, missing or throwing storage gives the default and never throws", () => {
    expect(loadMode(memoryStorage({ [MODE_STORAGE_KEY]: "purple" }))).toBe("dark");
    expect(loadMode(null)).toBe("dark");
    const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } };
    expect(loadMode(broken)).toBe("dark");
    expect(() => saveMode("light", broken)).not.toThrow();
    expect(() => saveMode("light", null)).not.toThrow();
  });
  it("sets data-mode on the root element and leaves data-palette alone", () => {
    const el = document.createElement("div");
    applyPalette("mint", el);
    applyMode("light", el);
    expect(el.getAttribute("data-mode")).toBe("light");
    expect(el.getAttribute("data-palette")).toBe("mint");
    applyMode("dark", el);
    expect(el.getAttribute("data-mode")).toBe("dark");
  });
});
