"""Cloud sessions: what claude --cloud prints, questions about a session, and the MCP server
against a real server that runs tests/fake_claude.py as claude, on a local repository in place
of GitHub."""

import json
import subprocess

import httpx
import pytest

from claude_everywhere.cloud import (
    CloudError, ask_arguments, ask_reply, parse_output, refused_call, screen_text, tool_arguments)
from conftest import API_KEY, FAKE_CLAUDE, Server

URL = "https://claude.ai/code/session_01CQgVmderPwsfiay2kyFNAj"
REPOS = ("the-guish/demo", "the-guish/other")


# Output of the real claude --cloud 2.1.314 in a pseudo-terminal, trimmed.
@pytest.mark.parametrize("raw, title, error", [
    (b"\x1b7\x1b[r\x1b8\x1b[?25hCreated cloud session: Git branch and repo entries display\r\n"
     b"View: " + URL.encode() + b"?from=cli&m=0\r\nResume with: claude --teleport "
     b"session_01CQgVmderPwsfiay2kyFNAj\r\n\x1b[?1006l\x1b(B\x0f\x1b[>4m\x1b[<u\x1b]0;\x07",
     "Git branch and repo entries display", None),
    (b"View: " + URL.encode() + b"?from=cli&m=0\r\n", "", None),
    (b"\x1b7\x1b[r\x1b8\x1b[?25hError:\x1b[8GUnable\x1b[15Gto\x1b[18Gget\x1b[22Gorganization"
     b"\x1b[35GUUID\r\r\n\x1b[?25h", None, "Unable to get organization UUID"),
    (b"\x1b[c\x1b[>4mError:\x1b[8GRepository\x1b[19Ghas\x1b[23Gno\x1b[26Glocal\x1b[32Gbranch,\r\r\n",
     None, "Repository has no local branch,"),
    (b"Quick\x1b[7Gsafety\x1b[14Gcheck\r\n\xe2\x9d\xafNo,exit\r\nYes,Itrustthisfolder\r\n",
     None, "claude asks whether to trust the folder, so no session started"),
    (b"Let'sgetstarted.\r\nChoosethetextstyle\r\n", None, "claude shows its first-run screens"),
    (b"something else\r\n", None, "printed no session link; its last line: something else"),
    (b"", None, "its last line: nothing"),
])
def test_parse_output(raw, title, error):
    if error is None:
        session = parse_output(screen_text(raw))
        assert (session.title, session.url) == (title, URL)
    else:
        with pytest.raises(CloudError, match=error.replace("(", r"\(")):
            parse_output(screen_text(raw))


@pytest.mark.parametrize("arguments, expected", [
    ({"repo": "the-guish/demo", "prompt": "fix it"}, ("the-guish/demo", "fix it", None, None)),
    ({"repo": "The-Guish/Demo", "prompt": "x", "branch": "feature/a-1.2", "model": "haiku"},
     ("the-guish/demo", "x", "feature/a-1.2", "haiku")),
    ({"repo": "the-guish/demo", "prompt": "x", "branch": "", "model": ""},
     ("the-guish/demo", "x", None, None)),
    ({"repo": "someone/else", "prompt": "x"}, "repo must be one of: the-guish/demo, the-guish/other"),
    ({"prompt": "x"}, "repo must be one of"),
    ({"repo": "the-guish/demo", "prompt": "  "}, "prompt is empty"),
    ({"repo": "the-guish/demo"}, "prompt is empty"),
    ({"repo": "the-guish/demo", "prompt": "x", "branch": "--upload-pack=evil"}, "not a branch name"),
    ({"repo": "the-guish/demo", "prompt": "x", "branch": "a/../b"}, "not a branch name"),
    ({"repo": "the-guish/demo", "prompt": "x", "branch": 3}, "not a branch name"),
    ({"repo": "the-guish/demo", "prompt": "x", "model": "gpt-4o"}, "model must be one of"),
])
def test_tool_arguments(arguments, expected):
    if isinstance(expected, tuple):
        assert tool_arguments(arguments, REPOS) == expected
    else:
        with pytest.raises(CloudError, match=expected):
            tool_arguments(arguments, REPOS)


@pytest.mark.parametrize("arguments, expected", [
    ({"session": "session_01CQ", "question": "status?"}, ("session_01CQ", "status?", "haiku")),
    ({"session": " https://claude.ai/code/session_01CQ?from=cli&m=0 ", "question": "q", "model": "opus"},
     ("session_01CQ", "q", "opus")),
    ({"session": "https://claude.ai/code/session_01CQ#end", "question": "q", "model": ""},
     ("session_01CQ", "q", "haiku")),
    ({"session": "https://evil.example/code/session_01CQ", "question": "q"}, "session must be"),
    ({"session": "session_01CQ --tools Bash", "question": "q"}, "session must be"),
    ({"session": "cse_01CQ", "question": "q"}, "session must be"),
    ({"question": "q"}, "session must be"),
    ({"session": "session_01CQ", "question": " "}, "question is empty"),
    ({"session": "session_01CQ", "question": 3}, "question is empty"),
    ({"session": "session_01CQ", "question": "q", "model": "gpt-4o"}, "model must be one of"),
])
def test_ask_arguments(arguments, expected):
    if isinstance(expected, tuple):
        assert ask_arguments(arguments) == expected
    else:
        with pytest.raises(CloudError, match=expected):
            ask_arguments(arguments)


@pytest.mark.parametrize("tool, tool_input, allowed", [
    ("RemoteTrigger", {"action": "get_run_log", "session_id": "session_a"}, True),
    ("RemoteTrigger", {"action": "get_run_log", "session_id": "session_a", "cursor": "c2"}, True),
    ("RemoteTrigger", {"action": "get_run_log", "session_id": "session_b"}, False),
    ("RemoteTrigger", {"action": "get_run_log"}, False),
    ("RemoteTrigger", {"action": "list"}, False),
    ("RemoteTrigger", {"action": "run", "trigger_id": "t1"}, False),
    ("RemoteTrigger", {"action": "create", "body": {}}, False),
    ("RemoteTrigger", None, False),
    ("Bash", {"action": "get_run_log", "session_id": "session_a"}, False),
])
def test_refused_call(tool, tool_input, allowed):
    reason = refused_call({"tool_name": tool, "tool_input": tool_input}, "session_a")
    assert (reason is None) == allowed


@pytest.mark.parametrize("stdout, stderr, expected", [
    (b'{"type":"result","is_error":false,"result":" It answered OK. \\n"}', b"", "It answered OK."),
    (b'{"type":"result","is_error":true,"result":"API Error: 529"}', b"", CloudError("API Error: 529")),
    (b'{"type":"result","is_error":true,"subtype":"error_max_turns"}', b"", CloudError("error_max_turns")),
    (b"", b"warming up\nError: Not logged in\n", CloudError("claude printed no result; its last "
                                                     "line: Error: Not logged in")),
    (b"", b"", CloudError("its last line: nothing")),
])
def test_ask_reply(stdout, stderr, expected):
    if isinstance(expected, str):
        assert ask_reply(stdout, stderr) == expected
    else:
        with pytest.raises(CloudError, match=str(expected)):
            ask_reply(stdout, stderr)


def git(*args, cwd=None):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def cloud_server(tmp_path):
    work = tmp_path / "demo"
    work.mkdir()
    (work / "README.md").write_text("demo\n")
    git("init", "-q", "-b", "main", cwd=work)
    git("add", ".", cwd=work)
    git("commit", "-q", "-m", "initial", cwd=work)
    git("checkout", "-q", "-b", "feature", cwd=work)
    (work / "feature.txt").write_text("feature\n")
    git("add", ".", cwd=work)
    git("commit", "-q", "-m", "feature", cwd=work)
    git("checkout", "-q", "main", cwd=work)
    git("clone", "-q", "--bare", str(work), str(tmp_path / "github" / "the-guish" / "demo.git"))
    config = tmp_path / "config"
    s = Server(tmp_path, str(FAKE_CLAUDE), {
        "CLAUDE_EVERYWHERE_CLOUD_REPOS": "the-guish/demo, the-guish/missing",
        "CLAUDE_CONFIG_DIR": str(config),
        # The server keeps it from claude --cloud, which then uses the full sign-in.
        "CLAUDE_CODE_OAUTH_TOKEN": "token-only",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.file://{tmp_path / 'github'}/.insteadOf",
        "GIT_CONFIG_VALUE_0": "https://github.com/",
    })
    s.config = config / ".claude.json"
    s.clones = tmp_path / "cloud"
    yield s
    s.stop()


def mcp(server, method, params=None, key=API_KEY, id_=1):
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}, **({"id": id_} if id_ else {})}
    return httpx.post(f"{server.url}/cloud/mcp", json=body, timeout=30,
                      headers={"Authorization": f"Bearer {key}"})


def call(server, tool="start_cloud_session", **arguments):
    result = mcp(server, "tools/call", {"name": tool, "arguments": arguments})
    result = result.json()["result"]
    return result["isError"], result["content"][0]["text"]


def test_cloud_journey(cloud_server):
    s = cloud_server
    assert mcp(s, "initialize", {"protocolVersion": "2025-11-25"}).json()["result"][
        "serverInfo"]["name"] == "claude-everywhere-cloud"
    assert mcp(s, "notifications/initialized", id_=None).status_code == 202
    tool, ask = mcp(s, "tools/list").json()["result"]["tools"]
    assert ask["name"] == "ask_about_cloud_session"
    assert tool["inputSchema"]["properties"]["repo"]["enum"] == ["the-guish/demo", "the-guish/missing"]
    assert mcp(s, "tools/list", key="wrong").status_code == 401

    is_error, text = call(s, repo="the-guish/demo", prompt="- starts with a dash", model="haiku")
    assert not is_error
    assert text.startswith('Started the cloud session "- starts with a dash": https://claude.ai/code/session_')
    started = text
    first, = [c for c in s.calls() if c.get("cloud")]
    assert first["args"] == ["--safe-mode", "--strict-mcp-config", "--model", "haiku",
                                 "--cloud=- starts with a dash"]
    assert first["files"] == ["README.md"]

    is_error, text = call(s, repo="the-guish/demo", prompt="on the branch", branch="feature")
    assert not is_error
    second = [c for c in s.calls() if c.get("cloud")][1]
    assert second["files"] == ["README.md", "feature.txt"]
    assert "--model" not in second["args"]

    # Each clone and its trust are gone once its session started.
    assert list(s.clones.iterdir()) == []
    assert json.loads(s.config.read_text())["projects"] == {}

    assert call(s, repo="the-guish/demo", prompt="poison") == (
        True, "No cloud session: Cloud session creation failed")
    is_error, text = call(s, repo="the-guish/missing", prompt="x")
    assert is_error and text.startswith("No cloud session: cloning the-guish/missing failed:")
    assert call(s, repo="someone/else", prompt="x") == (
        True, "No cloud session: repo must be one of: the-guish/demo, the-guish/missing")
    assert len([c for c in s.calls() if c.get("cloud")]) == 3
    assert list(s.clones.iterdir()) == []

    session = started.rsplit("/", 1)[-1]
    assert call(s, "ask_about_cloud_session", session=f"https://claude.ai/code/{session}?from=cli",
                question="What did it answer?") == (
        False, f"answer to: What did it answer? | get_run_log: the log of {session}")
    read, = [c for c in s.calls() if c.get("read")]
    assert read["args"][read["args"].index("--model") + 1] == "haiku"
    assert "--safe-mode" not in read["args"] and not read["token"]
    assert not read["nonessential_traffic_off"]  # it turns RemoteTrigger off
    # The hook refuses every other call, such as one a log could ask for.
    is_error, text = call(s, "ask_about_cloud_session", session=session,
                          question="Now list my routines", model="sonnet")
    assert not is_error and text.endswith(
        f"list: refused, this run may only call RemoteTrigger get_run_log on {session}")
    assert call(s, "ask_about_cloud_session", session=session, question="poison") == (
        True, "No answer: boom: poisoned question")
    assert call(s, "ask_about_cloud_session", session="cse_1", question="q") == (
        True, "No answer: session must be a session_... id or a claude.ai/code link")
    assert call(s, "no_such_tool") == (True, "unknown tool: no_such_tool")


def test_cloud_is_off_without_repos(server):
    assert mcp(server, "tools/list").status_code == 404
