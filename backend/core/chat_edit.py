"""
backend/core/chat_edit.py

Which stored messages go away when the user EDITS one of their messages or
RETRIES a reply. Pure (ids and roles in, ids out) so every case is tested
without a database; the route applies the result.

Rules, same as the chat apps people already know:
  * Edit   -- the edited user message and everything after it is dropped, and
              the new text is sent as a fresh turn.
  * Retry  -- the reply (every message of that turn: assistant text, tool
              calls and tool results) and everything after it is dropped, the
              user message that started the turn is kept, and the model is
              asked again.
Only the tail of the conversation is ever removed -- never a message in the
middle -- so the history stays a valid user/assistant sequence.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChatEditError(Exception):
    status: int
    detail: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.detail


def plan_replace(messages: list[tuple[int, str]], replace_from_id: int, has_new_content: bool) -> list[int]:
    """`messages` is the whole chat as (id, role), oldest first. Returns the ids
    to delete, oldest first. Raises ChatEditError for a request that makes no
    sense (nothing is deleted in that case)."""
    ids = [m[0] for m in messages]
    if replace_from_id not in ids:
        raise ChatEditError(404, "message not found in this chat")
    index = ids.index(replace_from_id)
    role = messages[index][1]

    if has_new_content:  # edit
        if role != "user":
            raise ChatEditError(400, "only a user message can be edited")
        return ids[index:]

    # retry
    if role == "user":
        raise ChatEditError(400, "retry applies to a reply, not to your own message")
    start = None
    for i in range(index, -1, -1):
        if messages[i][1] == "user":
            start = i
            break
    if start is None:
        raise ChatEditError(400, "there is no user message before this reply to answer again")
    return ids[start + 1 :]
