/**
 * frontend/src/theme.ts
 *
 * The app's two colour palettes. Both sit on black; what differs is the one
 * gradient that carries the identity (primary buttons, the brand mark, the
 * active nav marker, later the gauges):
 *
 *   aqua  white -> blue
 *   mint  white -> light green
 *
 * The palette is a `data-palette` attribute on <html>; theme.css turns it into
 * CSS variables. The choice is a per-viewer convenience, so it lives in
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
