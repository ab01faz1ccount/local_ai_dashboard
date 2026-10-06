/**
 * frontend/src/chat-stream-utils.ts
 *
 * The live-chat state machine behind ChatView's streaming send: what the
 * backend's `user` / `delta` / `message` / `error` / `done` events (see
 * POST /chats/{id}/messages/stream) do to the list on screen. A pure
 * reducer, so every ordering and duplicate case is testable without a
 * browser or a network.
 */

import type { ChatMessageItem } from "./api";

export interface StreamState {
  messages: ChatMessageItem[];
  /** Text of the reply being generated right now (not stored yet). */
  liveText: string;
  streaming: boolean;
  error: string | null;
}

export type StreamEvent =
  | { type: "user"; message: ChatMessageItem }
  | { type: "delta"; text: string }
  | { type: "message"; message: ChatMessageItem }
  | { type: "error"; detail: string }
  | { type: "done" };

export function startStream(messages: ChatMessageItem[]): StreamState {
  return { messages, liveText: "", streaming: true, error: null };
}

/** Add a stored message unless one with that id is already shown -- the
 * same message can arrive both via the stream and via a later refetch. */
function withMessage(messages: ChatMessageItem[], message: ChatMessageItem): ChatMessageItem[] {
  return messages.some((m) => m.id === message.id) ? messages : [...messages, message];
}

export function applyStreamEvent(state: StreamState, ev: StreamEvent): StreamState {
  switch (ev.type) {
    case "user":
      return { ...state, messages: withMessage(state.messages, ev.message) };
    case "delta":
      return { ...state, liveText: state.liveText + ev.text };
    case "message":
      // The stored message supersedes the in-progress text it was built from.
      return { ...state, messages: withMessage(state.messages, ev.message), liveText: "" };
    case "error":
      return { ...state, error: ev.detail, liveText: "" };
    case "done":
      return { ...state, streaming: false, liveText: "" };
  }
}

/** Turns a parsed SSE frame into a StreamEvent, or null for anything this
 * client doesn't understand (a newer server may add events -- ignore them
 * rather than break). */
export function toStreamEvent(sse: { event: string; data: string }): StreamEvent | null {
  let payload: unknown;
  try {
    payload = JSON.parse(sse.data);
  } catch {
    return null;
  }
  if (typeof payload !== "object" || payload === null) return null;
  const p = payload as Record<string, unknown>;
  switch (sse.event) {
    case "user":
      return { type: "user", message: p as unknown as ChatMessageItem };
    case "message":
      return { type: "message", message: p as unknown as ChatMessageItem };
    case "delta":
      return typeof p.text === "string" ? { type: "delta", text: p.text } : null;
    case "error":
      return { type: "error", detail: typeof p.detail === "string" ? p.detail : "The reply failed." };
    case "done":
      return { type: "done" };
    default:
      return null;
  }
}
