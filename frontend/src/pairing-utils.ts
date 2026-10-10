/**
 * frontend/src/pairing-utils.ts
 *
 * Pure helpers that turn the two backend proofs -- the config read-back
 * (`ConfigCheckReport`) and the live request watch (`AgentPairing`) -- into
 * the lines the terminal header and Settings page show. Kept out of the
 * components so the wording/decision rules are testable without rendering.
 *
 * Meaning is never carried by color alone: every summary has its own icon.
 */

import type { AgentPairing, ConfigCheckReport } from "./api";

export type Tone = "ok" | "warn" | "wait";

export interface Summary {
  tone: Tone;
  icon: string;
  text: string;
}

/** One human line per setting the agent did NOT accept. */
export function configProblems(report: ConfigCheckReport | null | undefined): string[] {
  if (!report) return [];
  return report.checks
    .filter((c) => c.status === "mismatch")
    .map((c) => {
      const cloud = c.detail?.includes("cloud") ? " — هنوز شبیه یه مدل ابریه (مقدار پیش‌فرض قدیمی برنده شده)" : "";
      return `${c.key}: agent می‌گه «${(c.actual ?? "").trim()}» ولی باید «${c.expected ?? ""}» باشه${cloud}`;
    });
}

/** Headline for the config read-back, or null when there is nothing to say. */
export function configSummary(report: ConfigCheckReport | null | undefined): Summary | null {
  if (!report) return null;
  const bad = report.checks.filter((c) => c.status === "mismatch").length;
  if (bad > 0) {
    return { tone: "warn", icon: "⚠", text: `تنظیمات agent با runtime نمی‌خونه (${bad} مورد)` };
  }
  if (report.verified) {
    return { tone: "ok", icon: "✔", text: "تنظیمات agent از خود agent خونده و تأیید شد" };
  }
  return { tone: "wait", icon: "?", text: "تنظیمات agent قابل خوندن نبود — تأیید نشد (نه ردشده، نه تأییدشده)" };
}

/** Headline for the live pairing proof, or null when no watch is running. */
export function pairingSummary(p: AgentPairing | null | undefined): Summary | null {
  if (!p || p.status === "not_watching") return null;
  if (p.status === "paired") {
    const n = p.requests;
    return { tone: "ok", icon: "✔", text: `جفت‌شدن تأیید شد — ${n} درخواست از agent به runtime رسید` };
  }
  if (p.status === "runtime_offline") {
    return { tone: "warn", icon: "⚠", text: "runtime آنلاین نیست — agent به سرور خاموش وصل می‌شه" };
  }
  if (p.status === "in_flight") {
    return {
      tone: "wait",
      icon: "⏳",
      text: "runtime الان مشغول پردازش یک درخواست است (لاگ فقط بعد از پایان درخواست ثبت می‌شود؛ با CPU ممکن است چند دقیقه طول بکشد)",
    };
  }
  const note = p.rebaselined ? " (لاگ runtime ریست شد؛ از همین‌جا دوباره می‌شمرم)" : "";
  return {
    tone: "wait",
    icon: "⏳",
    text: `هنوز درخواستی از agent به runtime نرسیده — یه پیام توی ترمینال بفرست${note}`,
  };
}
