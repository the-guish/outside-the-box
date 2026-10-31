"""Pure functions: OpenAI messages to Claude turns, and conversation keys."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

TEXT_PART_TYPES = {"text", "input_text", "output_text"}
ROLE_ALIASES = {"developer": "system"}
MODEL_ALIASES = {"opus", "sonnet", "haiku", "fable"}
DEFAULT_MODEL_NAME = "claude-code"


@dataclass(frozen=True)
class Message:
    role: str
    text: str


@dataclass(frozen=True)
class Turn:
    system: str
    history: tuple[Message, ...]
    prompt: str
    parent_key: str


def content_text(content: object) -> str:
    """Text of an OpenAI message content: a string or a list of parts. Other parts are dropped."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict) and part.get("type") in TEXT_PART_TYPES:
                texts.append(str(part.get("text", "")))
        return "\n".join(texts)
    raise ValueError(f"unsupported message content: {type(content).__name__}")


def normalize(raw_messages: list[dict]) -> list[Message]:
    messages = []
    for raw in raw_messages:
        if not isinstance(raw, dict) or "role" not in raw:
            raise ValueError("each message needs a role")
        role = ROLE_ALIASES.get(raw["role"], raw["role"])
        messages.append(Message(role, content_text(raw.get("content")).strip()))
    return messages


def conversation_key(messages: list[Message] | tuple[Message, ...]) -> str:
    payload = json.dumps([[m.role, m.text] for m in messages], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def reply_key(messages: list[Message], reply: str) -> str:
    """Key under which the next request will look up the session that produced `reply`."""
    return conversation_key([*messages, Message("assistant", reply.strip())])


def split_turn(messages: list[Message]) -> Turn:
    if not messages or messages[-1].role != "user":
        raise ValueError("the last message must come from the user")
    earlier = messages[:-1]
    return Turn(
        system="\n\n".join(m.text for m in messages if m.role == "system" and m.text),
        history=tuple(m for m in earlier if m.role != "system"),
        prompt=messages[-1].text,
        parent_key=conversation_key(earlier),
    )


def transcript_prompt(history: tuple[Message, ...], prompt: str) -> str:
    """Prompt for a new session that has to catch up with a conversation it never saw."""
    if not history:
        return prompt
    lines = ["This is our conversation so far:", ""]
    for m in history:
        lines += [f"<{m.role}>", m.text, f"</{m.role}>"]
    lines += ["", "Reply to my next message, continuing the conversation:", "", prompt]
    return "\n".join(lines)


def cli_prompt(text: str) -> str:
    """Prompt as `claude -p` reads it. A leading space keeps `/usage` and the like from
    running as a slash command, so the model gets them as text."""
    return " " + text if text.startswith("/") else text


def resolve_model(requested: str | None, default: str | None) -> str | None:
    """Claude model for a request. Names Claude does not know, like gpt-4o, get the default."""
    if requested in MODEL_ALIASES or (
        requested and requested.startswith("claude-") and requested != DEFAULT_MODEL_NAME
    ):
        return requested
    return default
