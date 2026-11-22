#!/bin/sh
# The setup script of the cloud environment (../README.md, "The cloud environment"). The person
# pastes this whole file into the environment's setup script, in claude.ai's settings. It runs
# as root when a container is built, before Claude Code starts.
#
# It writes the tincan hook to /opt/tincan/tincan_hook.py, and adds its four hooks to Claude
# Code's user settings, ~/.claude/settings.json. The hooks act in a cloud session alone
# (CLAUDE_CODE_REMOTE=true), and only on a conversation that the claude-cloud engine started.
#
# The tincan repository: TINCAN_URL, an environment variable of the cloud environment, or the
# line below.
TINCAN_URL_DEFAULT=""
set -eu

prefix=${TINCAN_PREFIX:-/opt/tincan}
mkdir -p "$prefix" "$HOME/.claude"
printf '%s\n' "${TINCAN_URL:-$TINCAN_URL_DEFAULT}" > "$prefix/url"

cat > "$prefix/tincan_hook.py" <<'PYTHON'
"""The tincan hook: a cloud session's side of the claude-cloud engine.

    python3 tincan_hook.py session-start | prompt | post-tool | stop | beat

Each conversation writes one branch of the tincan repository, s/<conversation id>:
session.json names the cloud session, status.json says whether a turn is running, and
turns/NNNN.md holds the reply of turn NNNN. The hook acts only in a cloud session, and only
after a prompt that opens with the engine's marker, [tincan <conversation id> turn <n>].
"""

import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PREFIX = Path(__file__).resolve().parent
HOME = Path(os.environ.get("TINCAN_HOME") or Path.home() / ".tincan")
STATE = HOME / "state.json"
STAMP = HOME / "last-tool"
PID = HOME / "beat.pid"
REPO = HOME / "repo"
BEAT_SECONDS = float(os.environ.get("TINCAN_BEAT_SECONDS") or 300)
MARKER = re.compile(r"\A\s*\[tincan ([0-9a-f-]{36}) turn (\d+)\]")
PUSH_TRIES = 4


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def url() -> str:
    given = os.environ.get("TINCAN_URL", "").strip()
    if given:
        return given
    try:
        return (PREFIX / "url").read_text().strip()
    except OSError:
        return ""


def load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save(state: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    STATE.with_suffix(".tmp").write_text(json.dumps(state))
    STATE.with_suffix(".tmp").replace(STATE)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=check, timeout=120
    )


def branch_of(state: dict) -> str:
    return "s/" + state["conversation"]


def checkout(state: dict) -> None:
    """The working clone on the conversation's branch: the remote's, or a new one."""
    if not (REPO / ".git").is_dir():
        REPO.mkdir(parents=True, exist_ok=True)
        git("init", "--quiet")
        git("remote", "add", "origin", url())
    git("remote", "set-url", "origin", url())
    branch = branch_of(state)
    head = git("symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()
    born = git("rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode == 0
    if head == branch and born:
        return
    if git("fetch", "--quiet", "origin", branch, check=False).returncode == 0:
        git("checkout", "--quiet", "--force", "-B", branch, "FETCH_HEAD")
    else:
        git("checkout", "--quiet", "--orphan", branch)
        git("rm", "-r", "--quiet", "--cached", "--ignore-unmatch", ".")


def write(state: dict, files: dict, message: str) -> None:
    """Commits the files on the conversation's branch and pushes it, under the hook's lock."""
    HOME.mkdir(parents=True, exist_ok=True)
    with open(HOME / "lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        checkout(state)
        for name, text in files.items():
            path = REPO / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            git("add", "--", name)
        git(
            "-c", "user.name=tincan", "-c", "user.email=tincan@tincan.invalid",
            "commit", "--quiet", "--allow-empty", "-m", message,
        )
        branch = branch_of(state)
        for attempt in range(PUSH_TRIES):
            pushed = git("push", "--quiet", "origin", "HEAD:refs/heads/" + branch, check=False)
            if pushed.returncode == 0:
                return
            time.sleep(2**attempt)
            if git("fetch", "--quiet", "origin", branch, check=False).returncode == 0:
                git("rebase", "--quiet", "-X", "theirs", "FETCH_HEAD", check=False)
        raise RuntimeError("tincan: the push of " + branch + " failed")


def status(state: dict, working: bool) -> str:
    last_tool = None
    if STAMP.exists():
        last_tool = datetime.datetime.fromtimestamp(STAMP.stat().st_mtime, datetime.timezone.utc)
        last_tool = last_tool.isoformat(timespec="seconds")
    return json.dumps(
        {
            "state": "working" if working else "idle",
            "turn": state["turn"],
            "beat_at": now(),
            "last_tool_at": last_tool,
            "session": state.get("session", ""),
        },
        indent=2,
    ) + "\n"


def start_beat() -> None:
    """The heartbeat daemon, unless one runs: a process of its own, which outlives the hook."""
    try:
        os.kill(int(PID.read_text()), 0)
        return
    except (OSError, ValueError):
        pass
    HOME.mkdir(parents=True, exist_ok=True)
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "beat"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=open(HOME / "beat.log", "a"),
        start_new_session=True,
    )
    PID.write_text(str(child.pid))


def on_prompt(event: dict) -> None:
    found = MARKER.match(event.get("prompt") or "")
    if not found:
        return
    state = load()
    conversation, turn = found.group(1), int(found.group(2))
    session = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID", "")
    first = state.get("conversation") != conversation
    state.update(conversation=conversation, turn=turn, working=True, session=session)
    save(state)
    STAMP.unlink(missing_ok=True)
    files = {"status.json": status(state, working=True)}
    if first:
        link = "https://claude.ai/code/" + re.sub(r"^cse_", "session_", session) if session else ""
        files["session.json"] = json.dumps(
            {"conversation": conversation, "session": session, "url": link, "started_at": now()},
            indent=2,
        ) + "\n"
    write(state, files, "turn %d: started" % turn)
    start_beat()


def on_stop(event: dict) -> None:
    state = load()
    if not state.get("working") or event.get("stop_hook_active"):
        return
    state["working"] = False
    save(state)
    answer = event.get("last_assistant_message") or ""
    files = {
        "turns/%04d.md" % state["turn"]: answer,
        "status.json": status(state, working=False),
    }
    write(state, files, "turn %d: answered" % state["turn"])


def beat() -> None:
    """Every BEAT_SECONDS, while a turn runs: status.json, with the last tool call's time."""
    while True:
        time.sleep(BEAT_SECONDS)
        state = load()
        if state.get("working"):
            try:
                files = {"status.json": status(state, working=True)}
                write(state, files, "turn %d: beat" % state["turn"])
            except Exception as failed:  # the next beat tries again
                print(now(), failed, file=sys.stderr, flush=True)


def main(event_name: str) -> int:
    if os.environ.get("CLAUDE_CODE_REMOTE") != "true" or not url():
        return 0
    if event_name == "beat":
        beat()
        return 0
    if event_name == "post-tool":
        if load().get("working"):
            HOME.mkdir(parents=True, exist_ok=True)
            STAMP.touch()
        return 0
    if event_name == "session-start":
        if load().get("conversation"):
            start_beat()
        return 0
    event = json.load(sys.stdin)
    if event_name == "prompt":
        on_prompt(event)
    elif event_name == "stop":
        on_stop(event)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1]))
    except Exception as failed:  # a hook that fails never stops the session
        print("tincan:", failed, file=sys.stderr)
        sys.exit(0)
PYTHON

# The hooks, added to the user settings beside what they hold.
python3 - "$prefix/tincan_hook.py" "$HOME/.claude/settings.json" <<'PYTHON'
import json, sys
from pathlib import Path

hook, path = sys.argv[1], Path(sys.argv[2])
settings = json.loads(path.read_text()) if path.exists() else {}
hooks = settings.setdefault("hooks", {})
for event, name, timeout in [
    ("SessionStart", "session-start", 30),
    ("UserPromptSubmit", "prompt", 120),
    ("PostToolUse", "post-tool", 10),
    ("Stop", "stop", 180),
]:
    command = "python3 %s %s" % (hook, name)
    entries = hooks.setdefault(event, [])
    if not any(h.get("command") == command for e in entries for h in e.get("hooks", [])):
        entries.append({"hooks": [{"type": "command", "command": command, "timeout": timeout}]})
path.write_text(json.dumps(settings, indent=2) + "\n")
PYTHON
