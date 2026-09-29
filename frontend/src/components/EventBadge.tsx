/**
 * Square LED + label for one event type, same visual language as
 * StatusLed/VerificationBadge (styles.css: .status-led / .verify-led) --
 * an event's outcome should read as the same kind of fact as a runtime's
 * ONLINE/OFFLINE state, not a new "colored pill" vocabulary.
 *
 * Color comes from the event type's SUFFIX (the part after the last
 * dot), not its category: "runtime.crashed" and "inference.failed"
 * should both read as alarming, "runtime.started" and "model.loaded"
 * should both read as good -- the category prefix says WHAT happened,
 * the suffix says whether that's good news.
 */

type Tone = "ok" | "muted" | "bad" | "pending";

const SUFFIX_TONE: Record<string, Tone> = {
  started: "ok",
  completed: "ok",
  loaded: "ok",
  registered: "ok",
  server_registered: "ok",
  created: "ok",
  verified: "ok",
  verification_completed: "ok",
  approved: "ok",
  connected: "ok",

  stopped: "muted",
  unloaded: "muted",
  deleted: "muted",
  removed: "muted",
  server_removed: "muted",
  disconnected: "muted",

  crashed: "bad",
  failed: "bad",
  connect_failed: "bad",
  denied: "bad",

  requested: "pending",
  metrics: "pending",
};

function toneFor(eventType: string): Tone {
  const suffix = eventType.split(".").pop() ?? "";
  return SUFFIX_TONE[suffix] ?? "muted";
}

export function EventBadge({ eventType }: { eventType: string }) {
  return (
    <span className="event-led" data-tone={toneFor(eventType)}>
      {eventType}
    </span>
  );
}
