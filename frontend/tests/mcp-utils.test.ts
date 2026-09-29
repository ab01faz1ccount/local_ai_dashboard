import { describe, it, expect } from "vitest";
import {
  EMPTY_FORM, buildCreatePayload, buildUpdatePayload, commandPreview, formFromServer,
  parseArgLines, parseEnvLines, parseHeaderLines, type McpFormState,
} from "../src/mcp-utils";
import type { McpServer } from "../src/api";

const form = (o: Partial<McpFormState> = {}): McpFormState => ({ ...EMPTY_FORM, name: "s", command: "npx", ...o });
const server = (o: Partial<McpServer> = {}): McpServer => ({
  id: 1, name: "s", description: null, transport: "stdio", command: "npx", args: ["-y", "pkg"], env_keys: ["K"], url: null,
  header_keys: [], is_remote: false, enabled: true, status: "DISCONNECTED", last_error: null, tools_count: 0, server_info: {},
  source_system: "local", project_id: null, agent_id: null, session_id: null, created_at: "", updated_at: "", ...o,
});

describe("parseArgLines", () => {
  it("one argv entry per line, blank lines dropped, inner spaces kept", () => {
    expect(parseArgLines("-y\n@scope/pkg\n\n/path with spaces/dir\r\n")).toEqual(["-y", "@scope/pkg", "/path with spaces/dir"]);
  });
  it("does not split on spaces", () => {
    expect(parseArgLines("--flag=a b")).toEqual(["--flag=a b"]);
  });
  it("empty text -> no args", () => expect(parseArgLines("")).toEqual([]));
});

describe("parseEnvLines", () => {
  it("splits at the first '=' only", () => {
    expect(parseEnvLines("TOKEN=abc==\nURL=http://x/?a=b")).toEqual({ ok: true, value: { TOKEN: "abc==", URL: "http://x/?a=b" } });
  });
  it("ignores blanks and comments", () => {
    expect(parseEnvLines("# note\n\nA=1")).toEqual({ ok: true, value: { A: "1" } });
  });
  it("reports the line number for a bad line", () => {
    const r = parseEnvLines("A=1\nnoequals");
    expect(r).toEqual({ ok: false, error: "Line 2: expected KEY=VALUE." });
  });
  it("rejects invalid names and empty keys", () => {
    expect(parseEnvLines("1BAD=x").ok).toBe(false);
    expect(parseEnvLines("=x").ok).toBe(false);
    expect(parseEnvLines("has-dash=x").ok).toBe(false);
  });
  it("allows an empty value", () => expect(parseEnvLines("A=")).toEqual({ ok: true, value: { A: "" } }));
});

describe("parseHeaderLines", () => {
  it("splits at the first ':'", () => {
    expect(parseHeaderLines("Authorization: Bearer a:b\nX-Y: 1")).toEqual({ ok: true, value: { Authorization: "Bearer a:b", "X-Y": "1" } });
  });
  it("rejects lines without a name", () => {
    expect(parseHeaderLines("nocolon").ok).toBe(false);
    expect(parseHeaderLines(": v").ok).toBe(false);
  });
});

describe("buildCreatePayload", () => {
  it("stdio: command, args, env", () => {
    const r = buildCreatePayload(form({ argsText: "-y\npkg", envText: "K=v" }));
    expect(r).toEqual({ ok: true, value: { name: "s", description: null, enabled: true, transport: "stdio", command: "npx", args: ["-y", "pkg"], env: { K: "v" } } });
  });
  it("http: url + headers, no stdio fields", () => {
    const r = buildCreatePayload(form({ transport: "http", url: " http://127.0.0.1:1/mcp ", headersText: "A: b", command: "npx" }));
    expect(r).toEqual({ ok: true, value: { name: "s", description: null, enabled: true, transport: "http", url: "http://127.0.0.1:1/mcp", headers: { A: "b" } } });
  });
  it("requires name, command (stdio) or url (remote)", () => {
    expect(buildCreatePayload(form({ name: "  " })).ok).toBe(false);
    expect(buildCreatePayload(form({ command: " " })).ok).toBe(false);
    expect(buildCreatePayload(form({ transport: "sse", url: "" })).ok).toBe(false);
  });
  it("surfaces env/header parse errors", () => {
    expect(buildCreatePayload(form({ envText: "bad" })).ok).toBe(false);
    expect(buildCreatePayload(form({ transport: "sse", url: "http://x", headersText: "bad" })).ok).toBe(false);
  });
});

describe("buildUpdatePayload", () => {
  it("omits env entirely when nothing was added or removed (secrets untouched)", () => {
    const r = buildUpdatePayload(form(), false);
    expect(r.ok && "env" in r.value).toBe(false);
  });
  it("removed keys become null, new entries set, explicit value wins over removal", () => {
    const r = buildUpdatePayload(form({ envText: "NEW=1\nOLD=2", removedEnvKeys: ["GONE", "OLD"] }), false);
    expect(r.ok && r.value.env).toEqual({ GONE: null, OLD: "2", NEW: "1" });
  });
  it("same for headers on remote servers", () => {
    const r = buildUpdatePayload(form({ transport: "http", url: "http://x", headersText: "A: 1", removedHeaderKeys: ["B"] }), false);
    expect(r.ok && r.value.headers).toEqual({ B: null, A: "1" });
    expect(r.ok && "env" in r.value).toBe(false);
  });
  it("connection-locked: only name + description are sent", () => {
    const r = buildUpdatePayload(form({ description: "d", command: "changed", envText: "K=v" }), true);
    expect(r).toEqual({ ok: true, value: { name: "s", description: "d" } });
  });
  it("validates like create", () => {
    expect(buildUpdatePayload(form({ name: "" }), false).ok).toBe(false);
    expect(buildUpdatePayload(form({ command: "" }), false).ok).toBe(false);
  });
});

describe("commandPreview / formFromServer", () => {
  it("stdio shows the command line, remote shows the url", () => {
    expect(commandPreview(server())).toBe("npx -y pkg");
    expect(commandPreview(server({ transport: "http", command: null, args: [], url: "http://x/mcp" }))).toBe("http://x/mcp");
  });
  it("edit form starts with args one-per-line and never pre-fills secrets", () => {
    const f = formFromServer(server({ description: "d" }));
    expect(f.argsText).toBe("-y\npkg");
    expect(f.envText).toBe("");
    expect(f.headersText).toBe("");
    expect(f.removedEnvKeys).toEqual([]);
  });
});
