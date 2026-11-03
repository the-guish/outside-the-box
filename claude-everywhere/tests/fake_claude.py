#!/usr/bin/env python3
"""Stands in for `claude -p --output-format stream-json`. Keeps sessions on disk, like claude.

The reply is `reply <n> to: <last prompt line>`, where n counts the session's turns.
A prompt containing `poison` fails. One containing `linger` exits 5 s after its result.
`use ls and cat` on the last line calls those tools: client tools through the --mcp-config server, as claude
does, or WebSearch when --allowedTools lets it run. The reply then lists each result.
`fake_claude.py setup-token` prints $FAKE_SETUP_TOKEN the way `claude setup-token` does.
Each call is logged as a JSON line to $FAKE_CLAUDE_LOG.
"""

import json
import os
import re
import sys
import time
import urllib.request
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


class Mcp:
    def __init__(self, config):
        server = json.loads(config)["mcpServers"]["client"]
        self.url, self.headers = server["url"], server["headers"]
        self.calls = 0
        self.request("initialize", {"protocolVersion": "2025-11-25"})
        self.tools = [t["name"] for t in self.request("tools/list")["tools"]]

    def request(self, method, params=None, meta=None):
        self.calls += 1
        body = {"jsonrpc": "2.0", "id": self.calls, "method": method,
                "params": {**(params or {}), **({"_meta": meta} if meta else {})}}
        request = urllib.request.Request(self.url, json.dumps(body).encode(), method="POST",
                                         headers={**self.headers, "Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())["result"]


mcp = Mcp(option("--mcp-config")) if option("--mcp-config") else None
allowed = (option("--allowedTools") or "").split(",")

with open(os.environ["FAKE_CLAUDE_LOG"], "a") as log:
    log.write(json.dumps({"args": args, "prompt": prompt, "nonessential_traffic_off":
                          os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == "1",
                          "mcp_tools": mcp and mcp.tools,
                          "env": {k: os.environ.get(k) for k in (
                              "MCP_TOOL_TIMEOUT", "CLAUDE_CODE_DISABLE_CLAUDE_MDS")}}) + "\n")

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


def stream(event):
    emit({"type": "stream_event", "event": event})


emit({"type": "system", "subtype": "init"})
wanted = re.search(r"\buse ([\w ]+)", prompt.strip().splitlines()[-1])
names = wanted.group(1).split(" and ") if wanted else []
calls = [(f"toolu_{uuid.uuid4().hex[:12]}", name) for name in names
         if (mcp and name in mcp.tools) or name in allowed]
if calls:
    stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    stream({"type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "Let me check."}})
    stream({"type": "content_block_stop", "index": 0})
    for index, (call_id, name) in enumerate(calls, 1):
        tool = name if name in allowed else f"mcp__client__{name}"
        stream({"type": "content_block_start", "index": index,
                "content_block": {"type": "tool_use", "id": call_id, "name": tool, "input": {}}})
        stream({"type": "content_block_delta", "index": index,
                "delta": {"type": "input_json_delta", "partial_json": json.dumps({"q": name})}})
        stream({"type": "content_block_stop", "index": index})
    stream({"type": "message_delta", "delta": {"stop_reason": "tool_use"}})
    stream({"type": "message_stop"})
    outcomes = []
    for call_id, name in calls:
        if name in allowed:
            outcomes.append(f"{name}: results for {name}")
            continue
        result = mcp.request("tools/call", {"name": name, "arguments": {"q": name}},
                             {"claudecode/toolUseId": call_id})
        text = result["content"][0]["text"]
        outcomes.append(f"{name} failed: {text}" if result.get("isError") else f"{name}: {text}")
    reply = f"{reply} | {'; '.join(outcomes)}"
    half = len(reply) // 2
stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
for piece in (reply[:half], reply[half:]):
    stream({"type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": piece}})
emit({"type": "result", "subtype": "success", "is_error": False, "result": reply,
      "usage": {"input_tokens": 3, "cache_read_input_tokens": 7, "output_tokens": 5}})
if "linger" in prompt:
    time.sleep(5)
