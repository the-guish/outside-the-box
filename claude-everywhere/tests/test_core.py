import pytest

from claude_everywhere.core import (
    Message, cli_prompt, content_text, conversation_key, normalize, reply_key, resolve_model,
    split_turn, transcript_prompt,
)

U, A, S = "user", "assistant", "system"


@pytest.mark.parametrize("content, expected", [
    ("hi", "hi"),
    (None, ""),
    ([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}], "a\nb"),
    ([{"type": "image_url", "image_url": {"url": "x"}}, {"type": "text", "text": "a"}], "a"),
    ([{"type": "input_text", "text": "a"}, "b"], "a\nb"),
])
def test_content_text(content, expected):
    assert content_text(content) == expected


@pytest.mark.parametrize("raw, expected", [
    ([{"role": "user", "content": "  hi \n"}], [Message(U, "hi")]),
    ([{"role": "developer", "content": "be brief"}], [Message(S, "be brief")]),
    ([{"role": "assistant", "content": None, "tool_calls": []}], [Message(A, "")]),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("raw", [[{"content": "no role"}], ["text"], [{"role": "user", "content": 3}]])
def test_normalize_rejects(raw):
    with pytest.raises(ValueError):
        normalize(raw)


@pytest.mark.parametrize("messages, system, history, prompt", [
    ([Message(U, "q")], "", (), "q"),
    ([Message(S, "s1"), Message(U, "q1"), Message(A, "a1"), Message(S, "s2"), Message(U, "q2")],
     "s1\n\ns2", (Message(U, "q1"), Message(A, "a1")), "q2"),
])
def test_split_turn(messages, system, history, prompt):
    turn = split_turn(messages)
    assert (turn.system, turn.history, turn.prompt) == (system, history, prompt)
    assert turn.parent_key == conversation_key(messages[:-1])


@pytest.mark.parametrize("messages", [[], [Message(U, "q"), Message(A, "a")], [Message(S, "s")]])
def test_split_turn_rejects(messages):
    with pytest.raises(ValueError):
        split_turn(messages)


def test_reply_key_is_the_parent_key_of_the_next_turn():
    first = [Message(S, "s"), Message(U, "q1")]
    next_turn = split_turn([*first, Message(A, "a1"), Message(U, "q2")])
    assert reply_key(first, "a1\n") == next_turn.parent_key


@pytest.mark.parametrize("a, b, same", [
    ([Message(U, "q")], [Message(U, "q")], True),
    ([Message(U, "q")], [Message(A, "q")], False),
    ([Message(U, "a"), Message(U, "b")], [Message(U, "a b")], False),
])
def test_conversation_key(a, b, same):
    assert (conversation_key(a) == conversation_key(b)) is same


@pytest.mark.parametrize("history, prompt, expected", [
    ((), "q", "q"),
    ((Message(U, "q1"), Message(A, "a1")), "q2",
     "This is our conversation so far:\n\n<user>\nq1\n</user>\n<assistant>\na1\n</assistant>\n\n"
     "Reply to my next message, continuing the conversation:\n\nq2"),
])
def test_transcript_prompt(history, prompt, expected):
    assert transcript_prompt(history, prompt) == expected


@pytest.mark.parametrize("requested, default, expected", [
    ("opus", None, "opus"),
    ("claude-sonnet-5-5", "haiku", "claude-sonnet-5-5"),
    ("gpt-4o", "haiku", "haiku"),
    ("claude-code", None, None),
    (None, "sonnet", "sonnet"),
])
def test_resolve_model(requested, default, expected):
    assert resolve_model(requested, default) == expected


@pytest.mark.parametrize("text, expected", [
    ("/usage", " /usage"),
    ("/etc/hosts, what is it?", " /etc/hosts, what is it?"),
    ("what is /etc/hosts?", "what is /etc/hosts?"),
    ("", ""),
])
def test_cli_prompt(text, expected):
    assert cli_prompt(text) == expected
