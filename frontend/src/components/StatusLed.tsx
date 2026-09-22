import type { RuntimeState } from "../api";

/**
 * Small square indicator + label, styled like a physical status light on
 * server rack hardware -- deliberately not a rounded "pill badge" (see
 * styles.css .status-led). Color alone never carries the meaning: the
 * text label is always present too.
 */
export function StatusLed({ state }: { state: RuntimeState }) {
  return (
    <span className="status-led" data-state={state}>
      {state}
    </span>
  );
}
