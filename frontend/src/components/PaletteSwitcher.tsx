import { PALETTES, type Palette } from "../theme";

/** Two swatches: pick the colour of the app's gradient. Arrow keys move between them (radio group). */
export function PaletteSwitcher({ value, onChange }: { value: Palette; onChange: (p: Palette) => void }) {
  return (
    <div className="palette-switcher" role="radiogroup" aria-label="Colour palette">
      {PALETTES.map((p) => (
        <button
          key={p.id}
          type="button"
          role="radio"
          aria-checked={value === p.id}
          aria-label={p.hint}
          title={p.hint}
          className={`palette-swatch palette-swatch-${p.id}${value === p.id ? " selected" : ""}`}
          onClick={() => onChange(p.id)}
        />
      ))}
    </div>
  );
}
