/**
 * frontend/src/runtime-config-utils.ts
 *
 * Pure helpers between the stored `config_json` and the Settings form.
 * Kept out of SettingsPage so the rules are testable without rendering.
 *
 * Why they exist -- three ways Settings used to NOT do what it showed:
 *  1. Clearing a field sent nothing, the backend merged, and the old value
 *     stayed in force. A cleared field is now sent as `null` ("forget it").
 *  2. KV offload / flash attention were checkboxes: ticking then unticking
 *     stored `false`, which meant "explicitly OFF" for KV offload, while a
 *     never-touched box looks identical. They are now explicit dropdowns
 *     (blank = llama.cpp's own default).
 *  3. Extra llama-server arguments had no field at all.
 */

export type ConfigValue = string | number | boolean | undefined;
export type ConfigForm = Record<string, ConfigValue>;

function isEmpty(v: unknown): boolean {
  return v === "" || v === undefined || v === null;
}

/** Stored config -> what the form shows. */
export function configToForm(config: Record<string, unknown> | null | undefined): ConfigForm {
  const form: Record<string, unknown> = { ...(config ?? {}) };

  // one argument per line (a JSON value stays a single argument)
  if (Array.isArray(form.extra_args)) form.extra_args = form.extra_args.map(String).join("\n");

  // flash attention: older saves held a boolean. true -> "on"; an unticked
  // box (false) always meant "don't pass the flag" == unset.
  if (form.flash_attn === true) form.flash_attn = "on";
  else if (form.flash_attn === false) form.flash_attn = "";

  // KV offload: a stored boolean is an explicit choice -- show it as such.
  if (form.kv_offload === true) form.kv_offload = "on";
  else if (form.kv_offload === false) form.kv_offload = "off";

  return form as ConfigForm;
}

/**
 * Form -> the `config_json` PATCH body. Non-empty values are sent as they are;
 * a field that is now empty but HAD a stored value is sent as `null` so the
 * backend removes it. Keys the form doesn't manage are left untouched.
 */
export function formToConfigPatch(
  original: Record<string, unknown> | null | undefined,
  form: ConfigForm
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, raw] of Object.entries(form)) {
    let value: unknown = raw;
    if (key === "extra_args" && typeof raw === "string") {
      const lines = raw
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter((l) => l !== "");
      value = lines.length > 0 ? lines : "";
    }
    if (isEmpty(value)) {
      if (original && original[key] != null) out[key] = null;
      continue;
    }
    out[key] = value;
  }
  return out;
}
