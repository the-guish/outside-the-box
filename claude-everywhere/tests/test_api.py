"""The OpenAI SDK against a real server, which runs tests/fake_claude.py as claude."""

import time

import httpx
import openai
import pytest

from conftest import FAKE_CLAUDE, Server


def ask(server, messages, **kwargs):
    reply = server.client.chat.completions.create(model="gpt-4o", messages=messages, **kwargs)
    return reply.choices[0].message.content


def ask_streaming(server, messages, **kwargs):
    stream = server.client.chat.completions.create(
        model="gpt-4o", messages=messages, stream=True, **kwargs
    )
    return "".join(c.choices[0].delta.content or "" for c in stream if c.choices)


def user(text):
    return {"role": "user", "content": text}


def assistant(text):
    return {"role": "assistant", "content": text}


def resumed(call):
    args = call["args"]
    return args[args.index("--resume") + 1] if "--resume" in args else None


def test_conversation_journey(server):
    system = {"role": "system", "content": "Be brief."}

    # A new conversation starts a session, with the system prompt.
    convo = [system, user("hello")]
    assert ask(server, convo) == "reply 1 to: hello"
    first = server.calls()[-1]
    assert resumed(first) is None
    for flag in ("--safe-mode", "--strict-mcp-config", "--disable-slash-commands"):
        assert flag in first["args"]
    assert first["args"][first["args"].index("--system-prompt") + 1] == "You are a helpful assistant."
    assert first["nonessential_traffic_off"]
    assert first["args"][first["args"].index("--append-system-prompt") + 1] == "Be brief."
    assert first["prompt"] == "hello"

    # The next message resumes that session; claude gets only the new message.
    convo += [assistant("reply 1 to: hello"), user("how are you?")]
    assert ask_streaming(server, convo) == "reply 2 to: how are you?"
    second = server.calls()[-1]
    assert resumed(second) is not None
    assert second["prompt"] == "how are you?"
    assert "--append-system-prompt" not in second["args"]

    # A third turn, streamed, continues from the second reply.
    convo += [assistant("reply 2 to: how are you?"), user("bye")]
    assert ask_streaming(server, convo) == "reply 3 to: bye"
    third_session = resumed(server.calls()[-1])
    assert third_session != resumed(second)

    # Regenerating the third reply resumes the same parent again.
    assert ask(server, convo) == "reply 3 to: bye"
    assert resumed(server.calls()[-1]) == third_session

    # Editing the second message branches from the first reply.
    edited = convo[:3] + [user("what's new?")]
    assert ask(server, edited) == "reply 2 to: what's new?"
    assert resumed(server.calls()[-1]) == resumed(second)

    # The branch continues on its own.
    edited += [assistant("reply 2 to: what's new?"), user("tell me more")]
    assert ask(server, edited) == "reply 3 to: tell me more"

    # A failing turn is an API error, and the conversation goes on afterwards.
    with pytest.raises(openai.APIStatusError) as failure:
        ask(server, edited[:-1] + [user("poison")])
    assert failure.value.status_code == 502
    assert "boom" in failure.value.message
    assert ask(server, edited[:-1] + [user("retry")]) == "reply 3 to: retry"

    # Replies end with the answer, before claude exits.
    started = time.monotonic()
    assert ask(server, edited[:-1] + [user("linger")]) == "reply 3 to: linger"
    assert ask_streaming(server, edited[:-1] + [user("linger more")]) == "reply 3 to: linger more"
    assert time.monotonic() - started < 3

    # A message that looks like a slash command reaches claude as text.
    assert ask(server, [user("/usage")]) == "reply 1 to: /usage"
    assert server.calls()[-1]["prompt"] == " /usage"

    # A history the server never saw starts a session from a transcript.
    foreign = [user("from another app"), assistant("an answer from elsewhere"), user("go on")]
    assert ask(server, foreign) == "reply 1 to: go on"
    call = server.calls()[-1]
    assert resumed(call) is None
    assert "<assistant>\nan answer from elsewhere\n</assistant>" in call["prompt"]

    # That conversation then resumes like any other.
    foreign += [assistant("reply 1 to: go on"), user("and then?")]
    assert ask(server, foreign) == "reply 2 to: and then?"
    assert resumed(server.calls()[-1]) is not None


def test_sessions_survive_a_restart(tmp_path):
    convo = [user("hello")]
    first = Server(tmp_path, str(FAKE_CLAUDE))
    try:
        ask(first, convo)
    finally:
        first.stop()
    second = Server(tmp_path, str(FAKE_CLAUDE))
    try:
        convo += [assistant("reply 1 to: hello"), user("still there?")]
        assert ask(second, convo) == "reply 2 to: still there?"
    finally:
        second.stop()


def test_lost_session_falls_back_to_a_transcript(server, tmp_path):
    convo = [user("hello")]
    ask(server, convo)
    for session in (tmp_path / "fake-state").iterdir():
        session.unlink()
    convo += [assistant("reply 1 to: hello"), user("again")]
    assert ask(server, convo) == "reply 1 to: again"
    assert "<user>\nhello\n</user>" in server.calls()[-1]["prompt"]


def test_models_usage_and_parameters(server):
    assert [m.id for m in server.client.models.list()] == [
        "claude-code", "fable", "haiku", "opus", "sonnet"
    ]

    reply = server.client.chat.completions.create(
        model="haiku", messages=[user("q")], temperature=0.2, max_tokens=10
    )
    assert reply.usage.prompt_tokens == 10 and reply.usage.completion_tokens == 5
    assert server.calls()[-1]["args"][-2:] == ["--model", "haiku"]

    ask(server, [user("q")])
    assert "--model" not in server.calls()[-1]["args"]

    stream = server.client.chat.completions.create(
        model="opus", messages=[user("q")], stream=True, stream_options={"include_usage": True}
    )
    assert [c.usage.total_tokens for c in stream if c.usage] == [15]


def test_rejects_bad_requests(server):
    with pytest.raises(openai.AuthenticationError):
        openai.OpenAI(base_url=f"{server.url}/v1", api_key="wrong").models.list()
    with pytest.raises(openai.BadRequestError):
        ask(server, [user("q"), assistant("a")])
    response = httpx.post(f"{server.url}/v1/chat/completions", json={"model": "x"},
                          headers={"Authorization": "Bearer test-key"})
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
