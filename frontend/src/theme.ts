/**
 * frontend/src/theme.ts
 *
 * The app's colour choices: a palette (the accent) and a mode (the background).
 * They are independent, so all four combinations work: blue or green accent,
 * on a soft dark or a soft light background (neither is pure black or pure
 * white). What the palette changes is the one gradient that carries the
 * identity (primary buttons, the brand mark, the active nav marker, the gauges):
 *
 *   aqua  white -> blue
 *   mint  white -> light green
 *
 * Palette and mode are `data-palette` and `data-mode` attributes on <html>;
 * theme.css turns them into CSS variables. The choice is a per-viewer convenience, so it lives in
 * localStorage -- wrapped in try/catch because storage can be unavailable
 * (private window, blocked site data) and the app must still render.
 */

export type Palette = "aqua" | "mint";

export const PALETTES: { id: Palette; label: string; hint: string }[] = [
  { id: "aqua", label: "Blue", hint: "White to blue gradient" },
  { id: "mint", label: "Green", hint: "White to light green gradient" },
];

export const DEFAULT_PALETTE: Palette = "aqua";
export const PALETTE_STORAGE_KEY = "lacc.palette";

/** Any stored/unknown value -> a palette we actually have. */
export function normalizePalette(value: unknown): Palette {
  return value === "mint" || value === "aqua" ? value : DEFAULT_PALETTE;
}

type StorageLike = Pick<Storage, "getItem" | "setItem">;

function defaultStorage(): StorageLike | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null; // accessing localStorage itself can throw
  }
}

export function loadPalette(storage: StorageLike | null = defaultStorage()): Palette {
  try {
    return normalizePalette(storage?.getItem(PALETTE_STORAGE_KEY));
  } catch {
    return DEFAULT_PALETTE;
  }
}

export function savePalette(palette: Palette, storage: StorageLike | null = defaultStorage()): void {
  try {
    storage?.setItem(PALETTE_STORAGE_KEY, palette);
  } catch {
    // not remembered across visits; the palette still applies for this one
  }
}

export function applyPalette(palette: Palette, root: HTMLElement = document.documentElement): void {
  root.setAttribute("data-palette", palette);
}

// ---- Background mode: soft dark or soft light ----

export type Mode = "dark" | "light";

export const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: "light", label: "Light", hint: "Soft light background" },
  { id: "dark", label: "Dark", hint: "Soft dark background" },
];

export const DEFAULT_MODE: Mode = "dark";
export const MODE_STORAGE_KEY = "lacc.mode";

export function normalizeMode(value: unknown): Mode {
  return value === "light" || value === "dark" ? value : DEFAULT_MODE;
}

export function loadMode(storage: StorageLike | null = defaultStorage()): Mode {
  try {
    return normalizeMode(storage?.getItem(MODE_STORAGE_KEY));
  } catch {
    return DEFAULT_MODE;
  }
}

export function saveMode(mode: Mode, storage: StorageLike | null = defaultStorage()): void {
  try {
    storage?.setItem(MODE_STORAGE_KEY, mode);
  } catch {
    // not remembered across visits; the mode still applies for this one
  }
}

export function applyMode(mode: Mode, root: HTMLElement = document.documentElement): void {
  root.setAttribute("data-mode", mode);
}
