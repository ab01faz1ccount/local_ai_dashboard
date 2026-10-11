/**
 * frontend/src/chat-actions-utils.ts
 *
 * What the screen shows while an EDITED message or a RETRIED reply is being
 * answered. Mirrors backend/core/chat_edit.py (the server is the authority and
 * re-syncs afterwards; this only keeps the list honest in the meantime).
 */

import type { ChatMessageItem } from "./api";

type Row = Pick<ChatMessageItem, "id" | "role">;

/** Messages that remain after an edit of `messageId` (it and everything after
 * go) -- the new user message then arrives through the stream. */
export function keptForEdit<T extends Row>(messages: T[], messageId: number): T[] {
  const i = messages.findIndex((m) => m.id === messageId);
  return i < 0 ? messages : messages.slice(0, i);
}

/** Messages that remain after a retry of the reply at `messageId`: everything
 * up to and including the user message that began that turn. */
export function keptForRetry<T extends Row>(messages: T[], messageId: number): T[] {
  const i = messages.findIndex((m) => m.id === messageId);
  if (i < 0) return messages;
  for (let j = i; j >= 0; j--) {
    if (messages[j].role === "user") return messages.slice(0, j + 1);
  }
  return messages;
}

/** Only your own messages can be edited. */
export function canEdit(m: Row): boolean {
  return m.role === "user";
}

/** Replies can be retried. A tool result is part of a reply, not one itself. */
export function canRetry(m: Row): boolean {
  return m.role === "assistant";
}

/** Copy puts the text of a message on the clipboard; tool results are copyable too. */
export function copyableText(m: Pick<ChatMessageItem, "content">): string {
  return m.content;
}
