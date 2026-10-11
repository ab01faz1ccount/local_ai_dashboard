import pytest

from backend.core.chat_edit import ChatEditError, plan_replace

CHAT = [(1, "user"), (2, "assistant"), (3, "user"), (4, "assistant"), (5, "tool"), (6, "assistant")]


def test_edit_drops_the_message_and_everything_after():
    assert plan_replace(CHAT, 3, True) == [3, 4, 5, 6]


def test_edit_first_message_drops_everything():
    assert plan_replace(CHAT, 1, True) == [1, 2, 3, 4, 5, 6]


def test_edit_only_user_messages():
    with pytest.raises(ChatEditError) as e:
        plan_replace(CHAT, 2, True)
    assert e.value.status == 400


def test_retry_last_reply_drops_the_whole_turn_but_keeps_the_question():
    # reply 6 belongs to the turn that began at user message 3: assistant 4, tool 5, assistant 6 all go
    assert plan_replace(CHAT, 6, False) == [4, 5, 6]


def test_retry_from_the_assistant_message_in_the_middle_of_a_turn_gives_the_same_result():
    assert plan_replace(CHAT, 4, False) == [4, 5, 6]


def test_retry_an_older_reply_drops_the_later_conversation_too():
    assert plan_replace(CHAT, 2, False) == [2, 3, 4, 5, 6]


def test_retry_on_a_user_message_is_refused():
    with pytest.raises(ChatEditError) as e:
        plan_replace(CHAT, 3, False)
    assert e.value.status == 400


def test_unknown_message_is_a_404():
    with pytest.raises(ChatEditError) as e:
        plan_replace(CHAT, 99, True)
    assert e.value.status == 404


def test_retry_with_no_user_message_before_it_is_refused():
    with pytest.raises(ChatEditError):
        plan_replace([(1, "assistant"), (2, "assistant")], 2, False)


def test_retry_does_not_mutate_its_input():
    before = list(CHAT)
    plan_replace(CHAT, 6, False)
    assert CHAT == before
