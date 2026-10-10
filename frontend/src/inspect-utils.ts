/**
 * frontend/src/inspect-utils.ts
 *
 * Words for the runtime "proof" panel: where the model really runs and which
 * settings the llama-server process did not honour. Pure, so testable.
 */

import type { RuntimeInspect } from "./api";
import type { Summary } from "./pairing-utils";

export function placementSummary(report: RuntimeInspect | null | undefined): Summary | null {
  if (!report) return null;
  const o = report.observed;
  const layers = o.layers_offloaded != null && o.layers_total ? ` (${o.layers_offloaded}/${o.layers_total} لایه)` : "";
  switch (report.placement) {
    case "gpu":
      return { tone: "ok", icon: "✔", text: `مدل روی GPU اجرا می‌شود${layers}` };
    case "partial":
      return { tone: "warn", icon: "⚠", text: `فقط بخشی از مدل روی GPU است${layers}؛ بقیه روی CPU = کند` };
    case "cpu":
      return { tone: "warn", icon: "⚠", text: "مدل روی CPU اجرا می‌شود (GPU استفاده نشده)" };
    default:
      return { tone: "wait", icon: "⏳", text: "هنوز معلوم نیست مدل روی GPU است یا CPU (خطی برای offload در لاگ نیست)" };
  }
}

export function inspectProblems(report: RuntimeInspect | null | undefined): string[] {
  if (!report) return [];
  const lines = report.checks
    .filter((c) => c.status === "mismatch")
    .map((c) => `${c.key}: تنظیم «${c.expected ?? ""}» ولی واقعاً «${c.actual ?? "—"}»${c.detail ? ` (${c.detail})` : ""}`);
  return [...report.warnings, ...lines];
}

/** Said when the build itself has no GPU backend -- before any model is run. */
export function deviceWarning(devices: RuntimeInspect["devices"] | undefined): string | null {
  if (!devices || !devices.probed) return null; // couldn't run it: say nothing rather than guess
  if (devices.gpus.length > 0) return null;
  return "این llama-server هیچ GPU ای نمی‌بیند (--list-devices خالی است). با این نسخه مدل فقط روی CPU اجرا می‌شود؛ نسخه‌ی CUDA یا Vulkan را نصب کن.";
}
