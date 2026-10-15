import { MODES, type Mode } from "../theme";

/** Two circles: a soft light and a soft dark background. A radio group, like the palette one beside it. */
export function ModeSwitcher({ value, onChange }: { value: Mode; onChange: (m: Mode) => void }) {
  return (
    <div className="palette-switcher mode-switcher" role="radiogroup" aria-label="Background">
      {MODES.map((m) => (
        <button
          key={m.id}
          type="button"
          role="radio"
          aria-checked={value === m.id}
          aria-label={m.hint}
          title={m.hint}
          className={`palette-swatch mode-swatch mode-swatch-${m.id}${value === m.id ? " selected" : ""}`}
          onClick={() => onChange(m.id)}
        />
      ))}
    </div>
  );
}
