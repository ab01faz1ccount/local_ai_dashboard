/**
 * frontend/src/log-utils.ts
 *
 * Pure helpers for the llama-server output view in Logs: which lines are
 * warnings/errors, and filtering by what the user typed. Kept out of the
 * component so the rules are testable.
 */

export type LogLevel = "info" | "warn" | "error" | "debug" | "plain";

// llama-server lines look like `0.29.944.195 I slot launch_slot_: ...` -- time, level letter, tag.
const PREFIXED = /^\s*[\d.]+\s+([IWEDF])\s/;

export function lineLevel(line: string): LogLevel {
  const m = PREFIXED.exec(line);
  if (m) {
    switch (m[1]) {
      case "W":
        return "warn";
      case "E":
      case "F":
        return "error";
      case "D":
        return "debug";
      default:
        return "info";
    }
  }
  // not in the prefixed format (stderr from a crash, a Windows error, ...): judge by the words
  if (/\b(error|failed|fatal|exception|cannot|unable)\b/i.test(line)) return "error";
  if (/\bwarn(ing)?\b/i.test(line)) return "warn";
  return "plain";
}

export function countProblems(lines: string[]): { warn: number; error: number } {
  let warn = 0;
  let error = 0;
  for (const line of lines) {
    const level = lineLevel(line);
    if (level === "warn") warn++;
    else if (level === "error") error++;
  }
  return { warn, error };
}

/** Lines containing EVERY space-separated term (case-insensitive); optionally only warnings/errors. */
export function filterLogLines(lines: string[], query: string, onlyProblems = false): string[] {
  const terms = query
    .toLowerCase()
    .split(/\s+/)
    .filter((t) => t !== "");
  return lines.filter((line) => {
    if (onlyProblems) {
      const level = lineLevel(line);
      if (level !== "warn" && level !== "error") return false;
    }
    if (terms.length === 0) return true;
    const lower = line.toLowerCase();
    return terms.every((t) => lower.includes(t));
  });
}
