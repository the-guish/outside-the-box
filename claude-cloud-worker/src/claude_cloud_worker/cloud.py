"""The two ways the engine reaches claude.ai/code: ``claude --cloud`` starts a cloud session,
and ``claude -p --cloud <session>`` sends one a message.

``claude --cloud`` starts a session only on a terminal, so it runs in a pseudo-terminal. A
folder claude has never run in asks for trust first, and a configuration that never ran claude
shows its first-run screens: ``trust`` answers both in ``.claude.json`` before the run.

A cloud session needs a full sign-in, the one ``claude /login`` writes. With a token of
``claude setup-token`` or an API key in its environment, claude finds no organization, so both
commands run without them.

A session runs in the cloud environment that claude's setting ``remote.defaultEnvironmentId``
names. ``start`` gives it with ``--settings``, so the account's default environment stays as it
is. The setting takes an id: ``environment_id`` finds the id of a name in the account's list.

The sign-in may come from a claude that runs elsewhere: ``sign_in.py`` copies it.

The terminal's reading is the one of ``claude-everywhere/src/claude_everywhere/cloud.py``.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import re
import select
import struct
import termios
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

START_SECONDS = 120.0
"""How long ``claude --cloud`` may take to start a session."""
SEND_SECONDS = 120.0
"""How long ``claude -p --cloud`` may take to send a message."""
AFTER_LINK = 2.0
NAME_LENGTH = 80
"""The seconds claude may take to exit after it printed the session's link."""

ESCAPES = re.compile(r"\x1b\[[0-9;?<>=]*[A-Za-z~]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b[78=>]")
# claude moves the cursor between words instead of printing spaces.
CURSOR_MOVE = re.compile(r"\x1b\[\d*[CG]")
CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SESSION_URL = re.compile(r"https://claude\.ai/code/(session_\w+)")
ERROR = re.compile(r"Error: ?(.*)")
# Screens that wait for a keystroke. Compared without spaces, which cursor moves can replace.
WAITING = {
    "itrustthisfolder": "claude asks whether to trust the folder",
    "let'sgetstarted": "claude shows its first-run screens",
}
ENVIRONMENT_ID = re.compile(r"(env|ccpool)_\w+")
API = "https://api.anthropic.com"
"""Where claude lists the account's cloud environments, at ``/v1/environment_providers``."""
TOKEN_VARIABLES = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")


class CloudError(Exception):
    """claude started no session, or sent no message. The text says why."""


@dataclass(frozen=True, slots=True)
class CloudSession:
    id: str
    url: str


def environment() -> dict[str, str]:
    """The worker's environment without the tokens that hide the full sign-in."""
    env = {k: v for k, v in os.environ.items() if k not in TOKEN_VARIABLES}
    env["TERM"] = "xterm-256color"
    return env


def config_file() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) / ".claude.json" if base else Path.home() / ".claude.json"


def _config(name: str) -> dict:
    try:
        data = json.loads(config_file().with_name(name).read_text())
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def list_environments() -> list[dict]:
    """The account's cloud environments, as claude lists them, with the worker's sign-in."""
    signed = _config(".credentials.json").get("claudeAiOauth") or {}
    token = signed.get("accessToken")
    organization = signed.get("organizationUuid") or (
        _config(".claude.json").get("oauthAccount") or {}
    ).get("organizationUuid")
    if not token or not organization:
        raise CloudError("the worker has no sign-in of claude.ai: run claude /login in it")
    request = urllib.request.Request(
        f"{API}/v1/environment_providers?limit=100",
        headers={
            "Authorization": f"Bearer {token}",
            "x-organization-uuid": organization,
            "anthropic-version": "2023-06-01",
            "User-Agent": "claude-cloud-worker",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:
            listed = json.load(answer).get("environments")
    except urllib.error.HTTPError as refused:
        raise CloudError(f"claude.ai answered {refused.code} to the list of environments") from None
    except (OSError, ValueError) as failed:
        raise CloudError(f"the list of environments did not come: {failed}") from None
    return [e for e in listed or [] if isinstance(e, dict)]


async def environment_id(named: str) -> str:
    """The id of the cloud environment ``named``, which is an id already or a name."""
    if ENVIRONMENT_ID.fullmatch(named):
        return named
    listed = await asyncio.to_thread(list_environments)
    ids = [e["environment_id"] for e in listed if e.get("name") == named and e.get("environment_id")]
    if len(ids) == 1:
        return ids[0]
    names = ", ".join(sorted(str(e.get("name")) for e in listed)) or "none"
    if ids:
        raise CloudError(f"{len(ids)} cloud environments are named {named!r}: give the id of one")
    raise CloudError(f"no cloud environment is named {named!r}; the account has: {names}")


def trust(folder: Path, config: Path | None = None) -> None:
    """Trusts ``folder`` in claude's configuration, and marks the first run as done."""
    path = config or config_file()
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        data = {}
    project = data.setdefault("projects", {}).setdefault(str(folder), {})
    if project.get("hasTrustDialogAccepted") and data.get("hasCompletedOnboarding"):
        return
    project["hasTrustDialogAccepted"] = True
    data["hasCompletedOnboarding"] = True
    data.setdefault("theme", "dark")
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    part.write_text(json.dumps(data, indent=2))
    part.chmod(0o600)
    part.replace(path)


def screen_text(raw: bytes) -> str:
    """The lines a terminal shows for claude's output, without colors and cursor moves."""
    text = CURSOR_MOVE.sub(" ", raw.decode("utf-8", "replace"))
    text = CONTROLS.sub("", ESCAPES.sub("", text)).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.strip() for line in text.split("\n") if line.strip())


def waiting_screen(text: str) -> str | None:
    squashed = re.sub(r"\s", "", text).lower()
    return next((why for marker, why in WAITING.items() if marker in squashed), None)


def parse_started(text: str) -> CloudSession:
    """The session ``claude --cloud`` printed, or ``CloudError`` with what it printed instead."""
    found = SESSION_URL.search(text)
    if found:
        return CloudSession(found.group(1), found.group(0))
    error = ERROR.search(text)
    if error:
        raise CloudError(error.group(1).strip())
    why = waiting_screen(text)
    if why:
        raise CloudError(f"{why}, so no session started")
    last = text.splitlines()[-1:] or ["nothing"]
    raise CloudError(f"claude --cloud printed no session link; its last line: {last[0]}")


def parse_sent(out: str, err: str, code: int | None) -> CloudSession:
    """The session ``claude -p --cloud <session>`` answered for, or ``CloudError``."""
    try:
        answer = json.loads(out)
    except ValueError:
        answer = None
    if isinstance(answer, dict) and answer.get("ok") and answer.get("session_id"):
        return CloudSession(str(answer["session_id"]), str(answer.get("url") or ""))
    if isinstance(answer, dict) and answer.get("error"):
        raise CloudError(str(answer["error"]))
    said = (err or out).strip().splitlines()[-3:] or [f"exit {code}"]
    raise CloudError(" ".join(said))


def _read_screen(fd: int, deadline: float) -> bytes:
    """claude's output, until it exits, waits for a keystroke, or printed the link."""
    out = b""
    linked_at = None
    while (left := deadline - time.monotonic()) > 0:
        if linked_at and time.monotonic() - linked_at > AFTER_LINK:
            break
        ready, _, _ = select.select([fd], [], [], min(left, 0.5))
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:  # EIO: claude exited and closed the terminal
            break
        if not chunk:
            break
        out += chunk
        text = screen_text(out)
        if waiting_screen(text):
            break
        if not linked_at and SESSION_URL.search(text):
            linked_at = time.monotonic()
    return out


def session_name(prompt: str) -> str:
    """The name of a session on claude.ai: the start of the person's first message."""
    words = " ".join(prompt.split())
    return words if len(words) <= NAME_LENGTH else words[: NAME_LENGTH - 1].rstrip() + "…"


async def start(
    cli: str,
    folder: Path,
    message: str,
    model: str | None,
    environment_id: str = "",
    name: str = "",
) -> CloudSession:
    """Starts a cloud session on the repository of ``folder``, with ``message`` as its first
    prompt. It runs in the environment ``environment_id``, or in claude's default one. Without
    ``name``, claude.ai names the session after the start of ``message``."""
    trust(folder)
    args = [cli, *(["--model", model] if model else [])]
    if name:
        args += ["--name", name]
    if environment_id:
        chosen = {"remote": {"defaultEnvironmentId": environment_id}}
        args += ["--settings", json.dumps(chosen)]
    args.append(f"--cloud={message}")
    controller, terminal = pty.openpty()
    fcntl.ioctl(terminal, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=folder,
            env=environment(),
            stdin=terminal,
            stdout=terminal,
            stderr=terminal,
            start_new_session=True,
        )
    except BaseException:
        os.close(controller)
        raise
    finally:
        os.close(terminal)
    try:
        raw = await asyncio.to_thread(_read_screen, controller, time.monotonic() + START_SECONDS)
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()
        os.close(controller)
    return parse_started(screen_text(raw))


async def send(cli: str, folder: Path, session: str, message: str) -> CloudSession:
    """Sends ``message`` to the cloud session ``session``. It returns before the reply."""
    process = await asyncio.create_subprocess_exec(
        cli,
        "-p",
        message,
        "--cloud",
        session,
        "--output-format",
        "json",
        cwd=folder,
        env=environment(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), SEND_SECONDS)
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise
    return parse_sent(out.decode(errors="replace"), err.decode(errors="replace"), process.returncode)
