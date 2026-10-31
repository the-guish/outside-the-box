"""Runs `claude -p` and turns its stream-json output into text deltas and a result."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from .core import cli_prompt
from .settings import Settings

log = logging.getLogger(__name__)
STDOUT_LINE_LIMIT = 16 * 1024 * 1024
EXIT_GRACE = 30  # seconds a process may take to exit after its result
_exiting: set[asyncio.Task] = set()
# Replaces Claude Code's coding-agent prompt. The client's system messages are appended to it.
SYSTEM_PROMPT = "You are a helpful assistant."
# Answering needs no user configuration, slash commands or telemetry.
MINIMAL_ARGS = ["--safe-mode", "--strict-mcp-config", "--disable-slash-commands",
                "--system-prompt", SYSTEM_PROMPT]
MINIMAL_ENV = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}


class ClaudeError(Exception):
    pass


@dataclass
class RunResult:
    session_id: str
    text: str
    usage: dict = field(default_factory=dict)


def build_args(
    settings: Settings, *, resume: str | None, system: str | None, model: str | None
) -> list[str]:
    args = [
        settings.claude_bin, "-p",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--tools", *settings.tools, *MINIMAL_ARGS,
    ]
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
) -> AsyncIterator[str | RunResult]:
    """Yields each text delta as a str, then one RunResult. Raises ClaudeError on failure."""
    settings.workdir.mkdir(parents=True, exist_ok=True)
    args = build_args(settings, resume=resume, system=system, model=model)
    log.info("running %s", shlex.join(args))
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=settings.workdir,
        env={**os.environ, **MINIMAL_ENV},
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
        async with asyncio.timeout(settings.timeout):
            async for line in proc.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                session_id = event.get("session_id") or session_id
                kind = event.get("type")
                if kind == "stream_event":
                    delta = _text_delta(event["event"], text)
                    if delta:
                        text += delta
                        yield delta
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
