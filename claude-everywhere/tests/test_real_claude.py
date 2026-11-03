"""The real claude CLI, run by hand: RUN_REAL_CLAUDE=1 uv run pytest -m real_claude."""

import os
import shutil

import pytest

from conftest import Server

pytestmark = [
    pytest.mark.real_claude,
    pytest.mark.skipif(not os.environ.get("RUN_REAL_CLAUDE"), reason="set RUN_REAL_CLAUDE=1"),
]


def test_real_claude_remembers_the_conversation(tmp_path):
    server = Server(tmp_path, shutil.which("claude"), {"CLAUDE_EVERYWHERE_MODEL": "haiku"})
    try:
        convo = [
            {"role": "system", "content": "Answer in one short sentence."},
            {"role": "user", "content": "My secret word is PELICAN. Just say OK."},
        ]
        first = server.client.chat.completions.create(model="gpt-4o", messages=convo)
        convo += [
            {"role": "assistant", "content": first.choices[0].message.content},
            {"role": "user", "content": "What is my secret word? Reply with the word only."},
        ]
        stream = server.client.chat.completions.create(model="gpt-4o", messages=convo, stream=True)
        second = "".join(c.choices[0].delta.content or "" for c in stream if c.choices)
        assert "PELICAN" in second.upper()

        convo += [
            {"role": "assistant", "content": second},
            {"role": "user", "content": "Spell my secret word backwards. Reply with that only."},
        ]
        with server.anthropic.messages.stream(
            model="claude-code", max_tokens=100, system=convo[0]["content"], messages=convo[1:]
        ) as stream:
            assert "NACILEP" in "".join(stream.text_stream).upper()

        # A client tool: claude calls it, waits, and uses its result.
        tools = [{"name": "get_locker_code", "description": "Returns the code of a locker.",
                  "input_schema": {"type": "object", "properties": {"locker": {"type": "string"}},
                                   "required": ["locker"]}}]
        convo = [{"role": "user", "content": "What is the code of locker B7? Use the tool."}]
        first = server.anthropic.messages.create(model="claude-code", max_tokens=200, tools=tools,
                                                 messages=convo)
        assert first.stop_reason == "tool_use"
        call = next(b for b in first.content if b.type == "tool_use")
        assert (call.name, call.input) == ("get_locker_code", {"locker": "B7"})
        convo += [
            {"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in first.content]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call.id,
                                          "content": "The code is 4417."}]},
        ]
        second = server.anthropic.messages.create(model="claude-code", max_tokens=200, tools=tools,
                                                  messages=convo)
        assert second.stop_reason == "end_turn"
        assert "4417" in second.content[-1].text

        command = server.client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "/usage"}])
        assert "isn't available in this environment" not in command.choices[0].message.content
    finally:
        server.stop()
