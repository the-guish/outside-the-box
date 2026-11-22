"""The claude-cloud engine and the tincan hook, on real git, with a fake ``claude --cloud``
(fake_claude.py) that runs the real hook as a cloud session would."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from robinauts.agent_engines.contract.domain import (
    AgentDefinition,
    CheckpointNotFoundError,
    Done,
    EngineError,
    ModelConfig,
    ModelsConfig,
    ReasoningDelta,
    SessionExistsError,
    SessionNotFoundError,
    TextDelta,
)
from robinauts.agent_engines.contract.ports import (
    EngineSettings,
    ProviderKeyLookup,
    ToolSecretLookup,
    installed,
)

from claude_cloud_worker import __main__ as front
from claude_cloud_worker.engine import CloudEngine
from claude_cloud_worker.settings import Settings

HERE = Path(__file__).resolve().parent
SETUP = HERE.parent / "environment" / "setup.sh"
FAKE = HERE / "fake_claude.py"
AGENT = AgentDefinition(system_prompt="Say what you did.")


class _NoKeys(ProviderKeyLookup, ToolSecretLookup):
    def key_for(self, provider_id: str) -> str:
        return ""

    def secret_for(self, server_id: str) -> str:
        return ""


SETTINGS = EngineSettings(
    models=ModelsConfig(models={"opus": ModelConfig(id="opus", provider="cc", name="opus")}),
    keys=_NoKeys(),
    tool_secrets=_NoKeys(),
)


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A bare tincan repository, the hook as setup.sh installs it, and the fake's folder."""
    remote = tmp_path / "tincan.git"
    git("init", "--quiet", "--bare", "--initial-branch=main", str(remote))
    home = tmp_path / "root"
    home.mkdir()
    prefix = tmp_path / "opt"
    subprocess.run(
        ["sh", str(SETUP)],
        env=dict(os.environ, HOME=str(home), TINCAN_PREFIX=str(prefix), TINCAN_URL=""),
        check=True,
    )
    FAKE.chmod(FAKE.stat().st_mode | stat.S_IXUSR)
    cloud = tmp_path / "cloud"
    monkeypatch.setenv("FAKE_CLOUD", str(cloud))
    monkeypatch.setenv("FAKE_HOOK", str(prefix / "tincan_hook.py"))
    monkeypatch.setenv("TINCAN_URL", str(remote))
    monkeypatch.setenv("TINCAN_BEAT_SECONDS", "3600")
    monkeypatch.setenv("FAKE_MODE", "normal")
    # claude's own configuration, and the token the worker holds for the claude-code engine.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "the-worker-token")
    yield {"remote": remote, "home": home, "prefix": prefix, "cloud": cloud, "tmp": tmp_path}
    for pid in tmp_path.rglob("beat.pid"):
        try:
            os.kill(int(pid.read_text()), signal.SIGTERM)
        except (OSError, ValueError):
            pass


def engine_for(world, **timings) -> CloudEngine:
    settings = Settings(
        tincan_url=str(world["remote"]),
        state=world["tmp"] / "state",
        cli=str(FAKE),
        poll_seconds=0.1,
        poll_most_seconds=0.3,
        **timings,
    )
    return CloudEngine(SETTINGS, settings)


async def turn(engine, session, prompt, checkpoint, *, resume=False, timeout=30.0):
    return [
        event
        async for event in engine.stream(
            session,
            AGENT,
            prompt,
            model="opus",
            checkpoint_id=checkpoint,
            timeout_seconds=timeout,
            resume=resume,
        )
    ]


def first_link(events) -> str:
    return next(e.text for e in events if isinstance(e, ReasoningDelta) and "claude.ai" in e.text)


def calls(world) -> list[dict]:
    path = world["cloud"] / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_the_worker_finds_the_engine_beside_claude_code():
    assert {"claude-code", "claude-cloud"} <= set(installed())
    assert front.ENGINES == ("claude-code", "claude-cloud")


def test_setup_adds_the_four_hooks_once_and_keeps_the_other_settings(tmp_path):
    home = tmp_path / "root"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text('{"model": "opus"}')
    env = dict(os.environ, HOME=str(home), TINCAN_PREFIX=str(tmp_path / "opt"), TINCAN_URL="u")
    for _ in range(2):
        subprocess.run(["sh", str(SETUP)], env=env, check=True)
    settings = json.loads((home / ".claude" / "settings.json").read_text())
    assert settings["model"] == "opus"
    assert sorted(settings["hooks"]) == ["PostToolUse", "SessionStart", "Stop", "UserPromptSubmit"]
    assert all(len(entries) == 1 for entries in settings["hooks"].values())
    assert (tmp_path / "opt" / "url").read_text() == "u\n"


def test_the_hook_does_nothing_outside_a_cloud_session(world):
    conversation = uuid.uuid4()
    env = dict(os.environ, TINCAN_HOME=str(world["tmp"] / "local"))
    env.pop("CLAUDE_CODE_REMOTE", None)
    subprocess.run(
        [sys.executable, str(world["prefix"] / "tincan_hook.py"), "prompt"],
        input=json.dumps({"prompt": f"[tincan {conversation} turn 1]\nhi"}),
        text=True,
        env=env,
        check=True,
    )
    assert git("ls-remote", str(world["remote"])) == ""


def test_a_conversation_starts_a_cloud_session_then_follows_it_up(world):
    engine = engine_for(world)
    session = uuid.uuid4()

    async def run():
        await engine.setup()
        await engine.create(session)
        first = await turn(engine, session, "hello", None)
        done = first[-1]
        second = await turn(engine, session, "and then?", done.checkpoint_id)
        return first, second

    first, second = asyncio.run(run())
    assert isinstance(first[-1], Done)
    assert first[-1].text == "reply to: hello"
    assert TextDelta(text="reply to: hello") in first
    assert any(isinstance(e, ReasoningDelta) and "claude.ai/code/session_" in e.text for e in first)
    assert second[-1].text == "reply to: and then?"

    made, followed = calls(world)
    # A new session starts on a terminal, with the agent's model. A later turn needs none.
    assert made["args"][:2] == ["--model", "opus"]
    assert made["args"][2].startswith(f"--cloud=[tincan {session} turn 1]\nhello")
    assert "<instructions>\nSay what you did.\n</instructions>" in made["args"][2]
    assert followed["args"][:3] == ["-p", f"[tincan {session} turn 2]\nand then?", "--cloud"]
    assert followed["args"][3].startswith("session_")
    assert followed["args"][3] in first_link(first)
    # The worker's token would hide the full sign-in, so neither run gets it.
    assert made["tokens"] == followed["tokens"] == []
    config = json.loads((world["tmp"] / "config" / ".claude.json").read_text())
    assert config["hasCompletedOnboarding"] is True
    assert config["projects"][made["cwd"]]["hasTrustDialogAccepted"] is True
    assert Path(made["cwd"]) == world["tmp"] / "state" / "tincan"

    branch = f"s/{session}"
    clone = world["tmp"] / "check"
    git("clone", "--quiet", "--branch", branch, str(world["remote"]), str(clone))
    assert (clone / "turns" / "0001.md").read_text() == "reply to: hello"
    assert (clone / "turns" / "0002.md").read_text() == "reply to: and then?"
    status = json.loads((clone / "status.json").read_text())
    assert status["state"] == "idle"
    assert status["turn"] == 2
    assert status["last_tool_at"]
    started = json.loads((clone / "session.json").read_text())
    assert started["conversation"] == str(session)


def test_a_resumed_turn_that_finished_gives_its_reply_again_and_sends_nothing(world):
    engine = engine_for(world)
    session = uuid.uuid4()

    async def run():
        await engine.create(session)
        first = await turn(engine, session, "hello", None)
        again = await turn(engine, session, "hello", None, resume=True)
        return first, again

    first, again = asyncio.run(run())
    assert again[-1] == first[-1]
    assert len(calls(world)) == 1


def test_a_cloud_session_goes_on_from_its_latest_reply_alone(world):
    engine = engine_for(world)
    session, other = uuid.uuid4(), uuid.uuid4()

    async def run():
        await engine.create(session)
        with pytest.raises(SessionExistsError):
            await engine.create(session)
        one = (await turn(engine, session, "one", None))[-1].checkpoint_id
        await turn(engine, session, "two", one)
        with pytest.raises(EngineError, match="latest reply"):
            await turn(engine, session, "edit", one)
        with pytest.raises(CheckpointNotFoundError):
            await turn(engine, session, "x", "not-a-checkpoint")
        with pytest.raises(SessionNotFoundError):
            await turn(engine, other, "x", None)
        with pytest.raises(EngineError, match="forked"):
            await engine.fork(session, other, checkpoint_id=one)
        await engine.forget(session)
        await engine.forget(session)
        return await engine.exists(session)

    assert asyncio.run(run()) is False


def test_a_session_that_never_takes_the_prompt_fails_the_turn(world, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "deaf")
    engine = engine_for(world, start_seconds=1.0)
    session = uuid.uuid4()

    async def run():
        await engine.create(session)
        await turn(engine, session, "hello", None)

    with pytest.raises(EngineError, match="took no prompt"):
        asyncio.run(run())


def test_a_session_that_goes_silent_fails_the_turn(world, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "silent")
    engine = engine_for(world, silent_seconds=1.5)
    session = uuid.uuid4()

    async def run():
        await engine.create(session)
        await turn(engine, session, "hello", None)

    with pytest.raises(EngineError, match="silent"):
        asyncio.run(run())


def test_the_turn_ends_at_its_timeout(world, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "silent")
    engine = engine_for(world)
    session = uuid.uuid4()

    async def run():
        await engine.create(session)
        await turn(engine, session, "hello", None, timeout=1.0)

    with pytest.raises(TimeoutError):
        asyncio.run(run())


def test_a_refused_prompt_fails_the_turn_and_records_nothing(world, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "refuse")
    engine = engine_for(world)
    session = uuid.uuid4()

    async def run():
        await engine.create(session)
        with pytest.raises(EngineError, match="not signed in"):
            await turn(engine, session, "hello", None)

    asyncio.run(run())
    record = json.loads((world["tmp"] / "state" / "sessions" / f"{session}.json").read_text())
    assert record["turns"] == []


def test_without_a_tincan_repository_every_turn_says_what_to_set(world):
    engine = CloudEngine(
        SETTINGS, Settings(tincan_url="", state=world["tmp"] / "state", cli=str(FAKE))
    )
    session = uuid.uuid4()

    async def run():
        await engine.create(session)
        await turn(engine, session, "hello", None)

    with pytest.raises(EngineError, match="CLAUDE_CLOUD_TINCAN_URL"):
        asyncio.run(run())


def test_the_heartbeat_writes_the_status_while_a_turn_runs(world, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "silent")
    monkeypatch.setenv("TINCAN_BEAT_SECONDS", "0.5")
    session = uuid.uuid4()
    subprocess.run(
        [str(FAKE), "--cloud-side", "session_beat", f"[tincan {session} turn 1]\nhello"],
        check=True,
    )
    branch = f"refs/heads/s/{session}"

    def commits() -> int:
        heads = git("ls-remote", str(world["remote"]), branch)
        if not heads:
            return 0
        return int(git("--git-dir", str(world["remote"]), "rev-list", "--count", branch))

    deadline = time.monotonic() + 20
    while commits() < 3 and time.monotonic() < deadline:
        time.sleep(0.2)
    assert commits() >= 3
    clone = world["tmp"] / "check"
    git("clone", "--quiet", "--branch", f"s/{session}", str(world["remote"]), str(clone))
    status = json.loads((clone / "status.json").read_text())
    assert status["state"] == "working"
    assert status["last_tool_at"]
    log = git("log", "--format=%s", cwd=clone).splitlines()
    assert log[-1] == "turn 1: started"
    assert "turn 1: beat" in log


def test_the_front_lets_the_worker_run_agents_on_both_engines(tmp_path):
    from robinauts.cli.composition import load

    agent = 'engine = "{}"\nmodels = ["cc-opus"]\nworker = "claude"\nsystem_prompt = "x"\n'
    config = tmp_path / "robinauts.toml"
    config.write_text(
        "\n".join(
            [
                '[model_providers.claude-code]\nkind = "anthropic"\napi_key_env = "NO_KEY"\n',
                '[models.cc-opus]\nprovider = "claude-code"\nname = "claude-opus-5-5"\n'
                'title = "Opus"\n',
                "[workers.claude]\nexternal = true\n",
                '[agents.claude-code]\ntitle = "Claude Code"\n' + agent.format("claude-code"),
                '[agents.cloud]\ntitle = "Cloud"\n' + agent.format("claude-cloud"),
                '[agents.other]\ntitle = "Other"\n' + agent.format("pydantic-ai"),
            ]
        )
    )
    loaded, _ = load(config, {})
    problems = front.configuration_problems("claude", loaded)
    assert problems == [
        "agent 'other' runs on engine 'pydantic-ai': this worker runs claude-code and claude-cloud"
    ]
