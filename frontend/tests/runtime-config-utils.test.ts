import { describe, it, expect } from "vitest";
import { configToForm, formToConfigPatch } from "../src/runtime-config-utils";

describe("configToForm", () => {
  it("null/undefined -> empty form", () => {
    expect(configToForm(null)).toEqual({});
    expect(configToForm(undefined)).toEqual({});
  });
  it("does not mutate the stored config", () => {
    const stored = { extra_args: ["--a", "b"], kv_offload: false };
    configToForm(stored);
    expect(stored).toEqual({ extra_args: ["--a", "b"], kv_offload: false });
  });
  it("extra_args list -> one argument per line", () => {
    expect(configToForm({ extra_args: ["--reasoning", "off"] }).extra_args).toBe("--reasoning\noff");
  });
  it("flash_attn: legacy true -> on, legacy false -> unset, strings kept", () => {
    expect(configToForm({ flash_attn: true }).flash_attn).toBe("on");
    expect(configToForm({ flash_attn: false }).flash_attn).toBe("");
    expect(configToForm({ flash_attn: "auto" }).flash_attn).toBe("auto");
  });
  it("kv_offload: a stored boolean is shown as the explicit choice it is", () => {
    expect(configToForm({ kv_offload: true }).kv_offload).toBe("on");
    expect(configToForm({ kv_offload: false }).kv_offload).toBe("off");
    expect(configToForm({}).kv_offload).toBeUndefined();
  });
  it("other keys pass through untouched", () => {
    expect(configToForm({ ctx_size: 16384, jinja: true, mcp_server_ids: [1, 2] })).toEqual({
      ctx_size: 16384,
      jinja: true,
      mcp_server_ids: [1, 2],
    });
  });
});

describe("formToConfigPatch", () => {
  it("sends non-empty values", () => {
    expect(formToConfigPatch({}, { ctx_size: 16384, n_gpu_layers: "99", jinja: true })).toEqual({
      ctx_size: 16384,
      n_gpu_layers: "99",
      jinja: true,
    });
  });
  it("a cleared field that had a stored value is sent as null so it is really removed", () => {
    const patch = formToConfigPatch({ ctx_size: 240000, threads: 6 }, { ctx_size: "", threads: 6 });
    expect(patch).toEqual({ ctx_size: null, threads: 6 });
  });
  it("undefined counts as cleared too", () => {
    expect(formToConfigPatch({ top_k: 20 }, { top_k: undefined })).toEqual({ top_k: null });
  });
  it("a field that was never set and is still empty is not sent at all", () => {
    expect(formToConfigPatch({}, { ctx_size: "" })).toEqual({});
    expect(formToConfigPatch({ a: 1 }, { ctx_size: "" })).toEqual({});
  });
  it("false and 0 are values, not 'cleared'", () => {
    expect(formToConfigPatch({ jinja: true, seed: 5 }, { jinja: false, seed: 0 })).toEqual({ jinja: false, seed: 0 });
  });
  it("keys the form doesn't contain are left alone", () => {
    expect(formToConfigPatch({ model_path: "/m.gguf", ctx_size: 1 }, { ctx_size: 2 })).toEqual({ ctx_size: 2 });
  });
  it("extra_args: text -> trimmed non-empty lines, kept as separate arguments", () => {
    const patch = formToConfigPatch({}, { extra_args: "--reasoning\n off \n\n{\"enable_thinking\":false}\r\n" });
    expect(patch.extra_args).toEqual(["--reasoning", "off", '{"enable_thinking":false}']);
  });
  it("extra_args: blank text clears a stored list", () => {
    expect(formToConfigPatch({ extra_args: ["--x"] }, { extra_args: " \n " })).toEqual({ extra_args: null });
  });
  it("extra_args: blank text with nothing stored sends nothing", () => {
    expect(formToConfigPatch({}, { extra_args: "" })).toEqual({});
  });
  it("round trip: load then save unchanged yields the same values (no spurious nulls)", () => {
    const stored = { ctx_size: 32768, extra_args: ["--a", "b"], flash_attn: true, kv_offload: false, jinja: true };
    const patch = formToConfigPatch(stored, configToForm(stored));
    expect(patch).toEqual({ ctx_size: 32768, extra_args: ["--a", "b"], flash_attn: "on", kv_offload: "off", jinja: true });
    expect(Object.values(patch)).not.toContain(null);
  });
  it("round trip: legacy flash_attn=false is dropped on save (it never meant anything)", () => {
    const stored = { flash_attn: false };
    expect(formToConfigPatch(stored, configToForm(stored))).toEqual({ flash_attn: null });
  });
  it("the fixed bug: ticking then unticking KV offload no longer hides --no-kv-offload", () => {
    // stored false -> the form shows "off" (visible), and choosing the blank option removes it
    const stored = { kv_offload: false };
    const form = configToForm(stored);
    expect(form.kv_offload).toBe("off");
    expect(formToConfigPatch(stored, { ...form, kv_offload: "" })).toEqual({ kv_offload: null });
  });
});
