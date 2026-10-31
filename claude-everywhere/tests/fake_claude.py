#!/usr/bin/env python3
"""Stands in for `claude -p --output-format stream-json`. Keeps sessions on disk, like claude.

The reply is `reply <n> to: <last prompt line>`, where n counts the session's turns.
A prompt containing `poison` fails. One containing `linger` exits 5 s after its result.
`fake_claude.py setup-token` prints $FAKE_SETUP_TOKEN the way `claude setup-token` does.
Each call is logged as a JSON line to $FAKE_CLAUDE_LOG.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

args = sys.argv[1:]
if args == ["setup-token"]:
    token = os.environ["FAKE_SETUP_TOKEN"]
    # Like the real screen: a prompt, then the token colored and wrapped, then it waits.
    sys.stdout.write("\x1b[1mBrowser didn't open? Use the url below to sign in\x1b[22m\r\n"
                     "https://claude.ai/oauth/authorize?code=true\r\nPaste code here if prompted > ")
    sys.stdout.flush()
    sys.stdin.readline()
    wrapped = "\x1b[39m\r\n\x1b[33m".join(token[i:i + 40] for i in range(0, len(token), 40))
    sys.stdout.write("\r\n\x1b[32m\u2713 Long-lived authentication token created successfully!\x1b[39m"
                     f"\r\nYour OAuth token (valid for 1 year):\r\n\x1b[33m{wrapped}\x1b[39m\r\n"
                     "\x1b[2mStore this token securely. You won't be able to see it again.\x1b[22m\r\n")
    sys.stdout.flush()
    time.sleep(60)
    sys.exit(0)
prompt = sys.stdin.read()
state = Path(os.environ["FAKE_CLAUDE_STATE"])
state.mkdir(parents=True, exist_ok=True)


def option(name):
    return args[args.index(name) + 1] if name in args else None


with open(os.environ["FAKE_CLAUDE_LOG"], "a") as log:
    log.write(json.dumps({"args": args, "prompt": prompt, "nonessential_traffic_off":
                          os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == "1"}) + "\n")

if "poison" in prompt:
    sys.exit("boom: poisoned prompt")

history = []
resume = option("--resume")
if resume:
    if not (state / resume).exists():
        sys.exit(f"No conversation found with session ID: {resume}")
    history = json.loads((state / resume).read_text())
    if "--fork-session" not in args:
        sys.exit("fake claude expects --fork-session with --resume")

session_id = str(uuid.uuid4())
history.append(prompt)
(state / session_id).write_text(json.dumps(history))

reply = f"reply {len(history)} to: {prompt.strip().splitlines()[-1]}"
half = len(reply) // 2


def emit(event):
    print(json.dumps({**event, "session_id": session_id}), flush=True)


emit({"type": "system", "subtype": "init"})
emit({"type": "stream_event", "event": {"type": "content_block_start",
                                        "content_block": {"type": "text", "text": ""}}})
for piece in (reply[:half], reply[half:]):
    emit({"type": "stream_event", "event": {"type": "content_block_delta",
                                            "delta": {"type": "text_delta", "text": piece}}})
emit({"type": "result", "subtype": "success", "is_error": False, "result": reply,
      "usage": {"input_tokens": 3, "cache_read_input_tokens": 7, "output_tokens": 5}})
if "linger" in prompt:
    time.sleep(5)
