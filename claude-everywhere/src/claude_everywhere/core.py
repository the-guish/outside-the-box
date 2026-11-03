"""Pure functions: OpenAI and Anthropic messages to Claude turns, and conversation keys."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

TEXT_PART_TYPES = {"text", "input_text", "output_text"}
ROLE_ALIASES = {"developer": "system"}
MODEL_ALIASES = {"opus", "sonnet", "haiku", "fable"}
DEFAULT_MODEL_NAME = "claude-code"
# Claude sees a bridged client tool `x` as `mcp__client__x`.
MCP_SERVER = "client"
MCP_PREFIX = f"mcp__{MCP_SERVER}__"
# Client tools that a server tool of the same purpose replaces.
SERVER_TOOL_REPLACES = {"WebSearch": "web_search", "WebFetch": "web_fetch"}


@dataclass(frozen=True)
class Message:
    role: str
    text: str


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False


@dataclass(frozen=True)
class Turn:
    system: str
    history: tuple[Message, ...]
    prompt: str
    parent_key: str


def content_text(content: object) -> str:
    """Text of a message content: a string or a list of parts. Tool calls and results become
    tags, so conversation keys and transcripts include them. Other parts are dropped."""
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
            elif isinstance(part, dict) and part.get("type") == "tool_use":
                arguments = json.dumps(part.get("input") or {}, ensure_ascii=False, sort_keys=True)
                texts.append(f'<tool_call id="{part.get("id")}" name="{part.get("name")}">'
                             f"{arguments}</tool_call>")
            elif isinstance(part, dict) and part.get("type") == "tool_result":
                error = ' error="true"' if part.get("is_error") else ""
                texts.append(f'<tool_result id="{part.get("tool_use_id")}"{error}>'
                             f'{content_text(part.get("content"))}</tool_result>')
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


def anthropic_messages(system: object, raw_messages: list[dict]) -> list[Message]:
    """Messages of an Anthropic request, with its system prompt first when it has one.
    The keys then match those of the same conversation sent through the OpenAI API."""
    if not isinstance(raw_messages, list):
        raise ValueError("messages must be a list")
    for raw in raw_messages:
        if not isinstance(raw, dict) or raw.get("role") not in ("user", "assistant"):
            raise ValueError("each message needs the role user or assistant")
    system_text = content_text(system).strip()
    return [Message("system", system_text)] * bool(system_text) + normalize(raw_messages)


def anthropic_tools(raw_tools: object, server_tools: tuple[str, ...] = ()) -> tuple[Tool, ...]:
    """Client tools of an Anthropic request. Anthropic's own server tools, which have a type,
    are dropped, and so are client tools that a server tool replaces."""
    if raw_tools is None:
        return ()
    if not isinstance(raw_tools, list):
        raise ValueError("tools must be a list")
    replaced = {SERVER_TOOL_REPLACES.get(t) for t in server_tools}
    tools = []
    for raw in raw_tools:
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            raise ValueError("each tool needs a name")
        if raw.get("type") not in (None, "custom") or raw["name"] in replaced:
            continue
        tools.append(Tool(raw["name"], raw.get("description") or "",
                          raw.get("input_schema") or {"type": "object"}))
    return tuple(tools)


def tool_results(raw_message: dict) -> dict[str, ToolResult]:
    """Tool results in an Anthropic user message, by the id of the call they answer."""
    content = raw_message.get("content")
    if not isinstance(content, list):
        return {}
    return {
        part["tool_use_id"]: ToolResult(content_text(part.get("content")).strip(),
                                        bool(part.get("is_error")))
        for part in content
        if isinstance(part, dict) and part.get("type") == "tool_result" and "tool_use_id" in part
    }


def bridged_name(name: str, tools: tuple[Tool, ...]) -> str | None:
    """Client tool behind a tool name Claude used, or None for Claude's own tools.
    Claude Code may replace characters an MCP tool name cannot have."""
    if not name.startswith(MCP_PREFIX):
        return None
    short = name.removeprefix(MCP_PREFIX)
    for tool in tools:
        if short in (tool.name, re.sub(r"[^A-Za-z0-9_-]", "_", tool.name)):
            return tool.name
    return short


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
