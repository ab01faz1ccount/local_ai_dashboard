/**
 * frontend/src/mcp-utils.ts
 *
 * Pure helpers behind the MCP form: turning the plain-text fields
 * (one arg per line, KEY=VALUE, Name: value) into the payloads
 * backend/api/mcp.py expects, and back. No React in here so it's cheap
 * to test exhaustively.
 *
 * Args are ONE PER LINE on purpose -- never a single command-line string
 * that gets split on spaces. The backend runs argv directly (no shell),
 * so "one line = one argv entry" is exactly what will be executed, and a
 * path with spaces needs no quoting.
 */

import type { McpServer, McpServerPayload, McpTransport } from "./api";

export interface McpFormState {
  name: string;
  description: string;
  transport: McpTransport;
  command: string;
  argsText: string;
  /** New / replaced env entries, one `KEY=VALUE` per line. */
  envText: string;
  url: string;
  /** New / replaced headers, one `Name: value` per line. */
  headersText: string;
  enabled: boolean;
  /** Existing env keys the user marked for removal (edit mode). */
  removedEnvKeys: string[];
  /** Existing header names the user marked for removal (edit mode). */
  removedHeaderKeys: string[];
}

export const EMPTY_FORM: McpFormState = {
  name: "",
  description: "",
  transport: "stdio",
  command: "",
  argsText: "",
  envText: "",
  url: "",
  headersText: "",
  enabled: true,
  removedEnvKeys: [],
  removedHeaderKeys: [],
};

export type ParseResult<T> = { ok: true; value: T } | { ok: false; error: string };

function nonBlankLines(text: string): { line: string; n: number }[] {
  return text
    .split("\n")
    .map((raw, i) => ({ line: raw.replace(/\r$/, ""), n: i + 1 }))
    .filter(({ line }) => line.trim() !== "" && !line.trim().startsWith("#"));
}

/** One argv entry per non-blank line; entries are kept verbatim
 * (no trimming inside a line, so intentional spaces survive). */
export function parseArgLines(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.replace(/\r$/, ""))
    .filter((l) => l.trim() !== "");
}

export function formatArgLines(args: string[]): string {
  return args.join("\n");
}

/** `KEY=VALUE` per line. Splits at the FIRST `=` so values may contain
 * `=` (base64, URLs). Blank lines and `# comments` are ignored. */
export function parseEnvLines(text: string): ParseResult<Record<string, string>> {
  const out: Record<string, string> = {};
  for (const { line, n } of nonBlankLines(text)) {
    const idx = line.indexOf("=");
    if (idx <= 0) return { ok: false, error: `Line ${n}: expected KEY=VALUE.` };
    const key = line.slice(0, idx).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) return { ok: false, error: `Line ${n}: "${key}" is not a valid variable name.` };
    out[key] = line.slice(idx + 1).trim();
  }
  return { ok: true, value: out };
}

/** `Name: value` per line, split at the FIRST `:` (values like
 * `Bearer abc` or URLs keep theirs). */
export function parseHeaderLines(text: string): ParseResult<Record<string, string>> {
  const out: Record<string, string> = {};
  for (const { line, n } of nonBlankLines(text)) {
    const idx = line.indexOf(":");
    if (idx <= 0) return { ok: false, error: `Line ${n}: expected "Header-Name: value".` };
    const key = line.slice(0, idx).trim();
    if (!key) return { ok: false, error: `Line ${n}: header name is empty.` };
    out[key] = line.slice(idx + 1).trim();
  }
  return { ok: true, value: out };
}

/** What the card shows as "what this server runs": the command line for
 * stdio, the URL for http/sse. Display only -- never executed from this string. */
export function commandPreview(s: Pick<McpServer, "transport" | "command" | "args" | "url">): string {
  if (s.transport === "stdio") return [s.command ?? "", ...s.args].join(" ").trim();
  return s.url ?? "";
}

export function formFromServer(s: McpServer): McpFormState {
  return {
    ...EMPTY_FORM,
    name: s.name,
    description: s.description ?? "",
    transport: s.transport,
    command: s.command ?? "",
    argsText: formatArgLines(s.args),
    url: s.url ?? "",
    enabled: s.enabled,
  };
}

function secretPatch(
  addedText: string,
  removed: string[],
  parse: (t: string) => ParseResult<Record<string, string>>
): ParseResult<Record<string, string | null> | undefined> {
  const parsed = parse(addedText);
  if (!parsed.ok) return parsed;
  const patch: Record<string, string | null> = {};
  for (const key of removed) patch[key] = null;
  Object.assign(patch, parsed.value); // an explicit new value wins over "remove"
  return { ok: true, value: Object.keys(patch).length ? patch : undefined };
}

/** Full config for POST. */
export function buildCreatePayload(f: McpFormState): ParseResult<McpServerPayload & { name: string }> {
  const name = f.name.trim();
  if (!name) return { ok: false, error: "Name is required." };
  const base = { name, description: f.description.trim() || null, enabled: f.enabled, transport: f.transport };

  if (f.transport === "stdio") {
    if (!f.command.trim()) return { ok: false, error: "Command is required for a stdio server." };
    const env = parseEnvLines(f.envText);
    if (!env.ok) return env;
    return { ok: true, value: { ...base, command: f.command.trim(), args: parseArgLines(f.argsText), env: env.value } };
  }
  if (!f.url.trim()) return { ok: false, error: "URL is required for an http/sse server." };
  const headers = parseHeaderLines(f.headersText);
  if (!headers.ok) return headers;
  return { ok: true, value: { ...base, url: f.url.trim(), headers: headers.value } };
}

/** Partial body for PATCH. While the server is connected the backend
 * refuses connection changes, so `connectionLocked` limits the payload to
 * the fields that are always editable. */
export function buildUpdatePayload(f: McpFormState, connectionLocked: boolean): ParseResult<McpServerPayload> {
  const name = f.name.trim();
  if (!name) return { ok: false, error: "Name is required." };
  const payload: McpServerPayload = { name, description: f.description.trim() || null };
  if (connectionLocked) return { ok: true, value: payload };

  payload.transport = f.transport;
  if (f.transport === "stdio") {
    if (!f.command.trim()) return { ok: false, error: "Command is required for a stdio server." };
    payload.command = f.command.trim();
    payload.args = parseArgLines(f.argsText);
    const env = secretPatch(f.envText, f.removedEnvKeys, parseEnvLines);
    if (!env.ok) return env;
    if (env.value) payload.env = env.value;
  } else {
    if (!f.url.trim()) return { ok: false, error: "URL is required for an http/sse server." };
    payload.url = f.url.trim();
    const headers = secretPatch(f.headersText, f.removedHeaderKeys, parseHeaderLines);
    if (!headers.ok) return headers;
    if (headers.value) payload.headers = headers.value;
  }
  return { ok: true, value: payload };
}
