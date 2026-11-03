"""Runs `claude -p` and turns its stream-json output into text deltas, client tool calls
and a result."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from .core import MCP_PREFIX, MCP_SERVER, cli_prompt
from .settings import Settings

log = logging.getLogger(__name__)
STDOUT_LINE_LIMIT = 16 * 1024 * 1024
EXIT_GRACE = 30  # seconds a process may take to exit after its result
_exiting: set[asyncio.Task] = set()
# Replaces Claude Code's coding-agent prompt. The client's system messages are appended to it.
SYSTEM_PROMPT = "You are a helpful assistant."
# Answering needs no user configuration, slash commands or telemetry.
MINIMAL_ARGS = ["--strict-mcp-config", "--disable-slash-commands", "--system-prompt", SYSTEM_PROMPT]
MINIMAL_ENV = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
# --safe-mode also turns off the --mcp-config server that bridges client tools. A run with
# client tools skips CLAUDE.md files, memory and hooks this way instead.
BRIDGE_ENV = {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
NO_HOOKS = json.dumps({"disableAllHooks": True})


class ClaudeError(Exception):
    pass


@dataclass(frozen=True)
class ToolUse:
    """A call to a client tool. Its name is the one Claude used, with the MCP prefix."""
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class AwaitingTools:
    """Claude ended a message with client tool calls and waits for their results."""
    ids: tuple[str, ...]


@dataclass
class RunResult:
    session_id: str
    text: str
    usage: dict = field(default_factory=dict)


def build_args(
    settings: Settings, *, resume: str | None, system: str | None, model: str | None,
    mcp_config: str | None = None,
) -> list[str]:
    tools = [t for t in (*settings.tools, *settings.server_tools) if t] or [""]
    args = [
        settings.claude_bin, "-p",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--tools", *tools, *MINIMAL_ARGS,
    ]
    allowed = [*([f"mcp__{MCP_SERVER}"] if mcp_config else []), *settings.server_tools]
    if allowed:
        args += ["--allowedTools", ",".join(allowed)]
    if mcp_config:
        args += ["--mcp-config", mcp_config, "--settings", NO_HOOKS]
    else:
        args.append("--safe-mode")
    if resume:
        args += ["--resume", resume, "--fork-session"]
    elif system:
        args += ["--append-system-prompt", system]
    if model:
        args += ["--model", model]
    return args + settings.extra_args


async def run_claude(
    settings: Settings,
    prompt: str,
    *,
    resume: str | None = None,
    system: str | None = None,
    model: str | None = None,
    mcp_config: str | None = None,
) -> AsyncIterator[str | ToolUse | AwaitingTools | RunResult]:
    """Yields each text delta as a str and each client tool call as a ToolUse, then one
    RunResult. A message that calls client tools ends with AwaitingTools: the consumer
    answers the calls through the bridge, then goes on iterating, possibly from another task.
    Raises ClaudeError on failure. The timeout counts the time between pauses."""
    settings.workdir.mkdir(parents=True, exist_ok=True)
    args = build_args(settings, resume=resume, system=system, model=model, mcp_config=mcp_config)
    log.info("running %s", shlex.join(args))
    env = {**os.environ, **MINIMAL_ENV}
    if mcp_config:
        # Claude Code gives up on a tool after a minute by default; the bridge decides instead.
        env |= {**BRIDGE_ENV, "MCP_TOOL_TIMEOUT": str(int((settings.tool_timeout + 60) * 1000))}
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=settings.workdir,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=STDOUT_LINE_LIMIT,
    )
    stderr_task = asyncio.create_task(proc.stderr.read())
    result = None
    try:
        proc.stdin.write(cli_prompt(prompt).encode())
        await proc.stdin.drain()
        proc.stdin.close()

        session_id = None
        text = ""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.timeout
        blocks: dict[int, dict] = {}  # tool_use blocks of the current message, by index
        calls: list[str] = []
        while line := await asyncio.wait_for(proc.stdout.readline(), deadline - loop.time()):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = event.get("session_id") or session_id
            kind = event.get("type")
            if kind == "stream_event":
                stream_event = event["event"]
                delta = _text_delta(stream_event, text)
                if delta:
                    text += delta
                    yield delta
                call = _tool_use(stream_event, blocks)
                if call:
                    calls.append(call.id)
                    yield call
                if stream_event.get("type") == "message_stop" and calls:
                    yield AwaitingTools(tuple(calls))
                    # The next segment is a new reply to the client.
                    calls, text = [], ""
                    deadline = loop.time() + settings.timeout
            elif kind == "result":
                # claude saves the session before printing the result, then takes
                # about a second to exit. The reply need not wait for that.
                result = event
                break

        if result is None:
            returncode = await proc.wait()
            stderr = (await stderr_task).decode(errors="replace").strip()
            raise ClaudeError(stderr[-2000:] or f"exit code {returncode}")
        if result.get("is_error"):
            raise ClaudeError(str(result.get("result") or "claude reported an error"))
        if not text and result.get("result"):
            text = result["result"]
            yield text
        yield RunResult(session_id=session_id, text=text, usage=result.get("usage") or {})
    except TimeoutError:
        raise ClaudeError(f"claude did not finish within {settings.timeout:.0f}s") from None
    finally:
        if proc.returncode is None and result is not None:
            task = asyncio.create_task(_wait_for_exit(proc, stderr_task))
            _exiting.add(task)
            task.add_done_callback(_exiting.discard)
        else:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            stderr_task.cancel()


async def _wait_for_exit(proc: asyncio.subprocess.Process, stderr_task: asyncio.Task) -> None:
    """Drains the output of a process that has answered, and kills it if it does not exit."""
    try:
        async with asyncio.timeout(EXIT_GRACE):
            await proc.stdout.read()
            await stderr_task
            await proc.wait()
    except TimeoutError:
        log.warning("claude (pid %s) did not exit after its result, killing it", proc.pid)
        proc.kill()
        await proc.wait()


def _tool_use(event: dict, blocks: dict[int, dict]) -> ToolUse | None:
    """Collects a client tool_use block; returns it complete at its content_block_stop.
    Claude's own tools, such as WebSearch, run inside claude and are not reported."""
    index = event.get("index")
    if event.get("type") == "content_block_start":
        block = event.get("content_block") or {}
        if block.get("type") == "tool_use" and block.get("name", "").startswith(MCP_PREFIX):
            blocks[index] = {"id": block["id"], "name": block["name"], "json": ""}
    elif event.get("type") == "content_block_delta" and index in blocks:
        delta = event.get("delta") or {}
        if delta.get("type") == "input_json_delta":
            blocks[index]["json"] += delta.get("partial_json", "")
    elif event.get("type") == "content_block_stop" and index in blocks:
        block = blocks.pop(index)
        return ToolUse(block["id"], block["name"], json.loads(block["json"] or "{}"))
    return None


def _text_delta(event: dict, text_so_far: str) -> str:
    """Text an Anthropic stream event adds to the reply. Separate text blocks get a blank line."""
    if event.get("type") == "content_block_start":
        block = event.get("content_block") or {}
        if block.get("type") == "text" and text_so_far and not text_so_far.endswith("\n\n"):
            return "\n\n"
    elif event.get("type") == "content_block_delta":
        delta = event.get("delta") or {}
        if delta.get("type") == "text_delta":
            return delta.get("text", "")
    return ""
