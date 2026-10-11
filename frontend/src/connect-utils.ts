import type {
  AutoFreeMode,
  AutoFreePolicy,
  CustomAgentPreset,
  CustomAgentSpec,
  HfFileCandidate,
  QuickConnectResult,
} from "./api";

export const AUTO_FREE_OPTIONS: { mode: AutoFreeMode; label: string; hint: string }[] = [
  { mode: "off", label: "Keep it loaded", hint: "The model stays in memory until you stop it." },
  { mode: "after_agent", label: "After the agent closes", hint: "Stops a little after the last agent terminal on it is closed." },
  { mode: "idle", label: "When idle", hint: "Stops after a while with no requests and no open agent terminal." },
];

export const MIN_MINUTES = 1;
export const MAX_MINUTES = 24 * 60;

/** Whole minutes in range, or null for anything else (empty, text, 0, too large). */
export function parseMinutes(text: string): number | null {
  const t = text.trim();
  if (!/^\d+$/.test(t)) return null;
  const n = Number(t);
  return n >= MIN_MINUTES && n <= MAX_MINUTES ? n : null;
}

export function describeAutoFree(p: AutoFreePolicy): string {
  if (p.mode === "after_agent") return `Frees memory ${p.grace_minutes} min after the last agent closes.`;
  if (p.mode === "idle") return `Frees memory after ${p.idle_minutes} min with no activity.`;
  return "Stays loaded until you stop it.";
}

/** The request body for a mode + the minutes box. Null when the minutes are invalid. */
export function autoFreePayload(
  mode: AutoFreeMode,
  minutesText: string
): { mode: AutoFreeMode; grace_minutes?: number; idle_minutes?: number } | null {
  if (mode === "off") return { mode };
  const minutes = parseMinutes(minutesText);
  if (minutes === null) return null;
  return mode === "after_agent" ? { mode, grace_minutes: minutes } : { mode, idle_minutes: minutes };
}

export function minutesFor(p: AutoFreePolicy): string {
  return String(p.mode === "after_agent" ? p.grace_minutes : p.idle_minutes);
}

/** A starting spec from a preset: the user supplies the name and the command. */
export function specFromPreset(preset: CustomAgentPreset, name: string, executable: string): CustomAgentSpec {
  return {
    name,
    executable,
    config: JSON.parse(JSON.stringify(preset.config)),
    ...(preset.settings ? { settings: { ...preset.settings } } : {}),
    ...(preset.env ? { env: { ...preset.env } } : {}),
  };
}

export function parseSpecJson(text: string): { spec: CustomAgentSpec } | { error: string } {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return { error: "This isn't valid JSON — check commas and quotes." };
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return { error: "The spec must be a JSON object." };
  }
  return { spec: value as CustomAgentSpec };
}

/** Files with the recommended one first; everything else keeps its order. */
export function recommendedFirst(files: HfFileCandidate[]): HfFileCandidate[] {
  return [...files.filter((f) => f.recommended), ...files.filter((f) => !f.recommended)];
}

export type Tone = "ok" | "warn" | "error";

/** One honest sentence about what quick-connect really did. */
export function quickConnectMessage(r: QuickConnectResult): { tone: Tone; text: string } {
  const verb = r.created_runtime ? "Created a runtime" : "Reused the existing runtime";
  if (!r.started && r.runtime.status !== "ONLINE") {
    return { tone: "error", text: `${verb}, but it did not start${r.error ? `: ${r.error}` : "."}` };
  }
  if (r.agent && r.verification && !r.verification.ok) {
    return { tone: "warn", text: `${verb} and started it, but the agent did not accept every setting — see the check below.` };
  }
  if (r.agent && r.error) return { tone: "warn", text: r.error };
  if (r.agent) return { tone: "ok", text: `${verb}, started it and configured ${r.agent.name} to use it.` };
  return { tone: "ok", text: `${verb} and started it.` };
}
