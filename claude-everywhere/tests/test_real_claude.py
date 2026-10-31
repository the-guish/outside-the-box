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

        command = server.client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "/usage"}])
        assert "isn't available in this environment" not in command.choices[0].message.content
    finally:
        server.stop()
