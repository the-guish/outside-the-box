"""The claude-cloud engine: a conversation is one cloud session of Claude Code, and a turn is
one prompt sent to it.

A turn sends its prompt with ``claude --cloud`` (``cloud.py``), then waits for the cloud session's reply on
tincan (``tincan.py``). The engine keeps one record per conversation: the cloud session, and
each turn with its checkpoint and its reply.

A cloud session goes on from its latest reply alone. A turn from an earlier checkpoint, as an
edit or a regenerate asks, is refused, and so is a fork.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncGenerator, Mapping
from pathlib import Path
from typing import Any

from robinauts.agent_engines.contract.domain import (
    AgentDefinition,
    CheckpointNotFoundError,
    Done,
    EngineError,
    Event,
    ProviderKind,
    ReasoningDelta,
    SessionExistsError,
    SessionNotFoundError,
    TextDelta,
    UnknownModelError,
)
from robinauts.agent_engines.contract.ports import AgentEngine, EngineSettings, StorageConfig

from claude_cloud_worker import cloud, tincan
from claude_cloud_worker.settings import TINCAN_URL, Settings, settings_from

ENGINE = "claude-cloud"


def init_claude_cloud(settings: EngineSettings, storage: StorageConfig) -> AgentEngine:
    """The entry point, under ``robinauts.engines``. The storage is not used: the records are
    in the engine's own folder."""
    return CloudEngine(settings, settings_from(os.environ))


class CloudEngine(AgentEngine):
    def __init__(self, settings: EngineSettings, container: Settings) -> None:
        self._settings = settings
        self._container = container
        self._tincan = tincan.Tincan(container.tincan_url, container.state / "tincan")
        self._poller = tincan.Poller(
            self._tincan, container.poll_seconds, container.poll_most_seconds
        )

    def kinds(self) -> frozenset[ProviderKind]:
        return frozenset({ProviderKind.ANTHROPIC, ProviderKind.ANTHROPIC_COMPATIBLE})

    # --- the records ---------------------------------------------------------------------------

    def _record_path(self, session_id: uuid.UUID) -> Path:
        return self._container.state / "sessions" / f"{session_id}.json"

    def _load(self, session_id: uuid.UUID) -> dict[str, Any]:
        try:
            return json.loads(self._record_path(session_id).read_text())
        except FileNotFoundError:
            raise SessionNotFoundError(f"no cloud session for {session_id}") from None

    def _save(self, session_id: uuid.UUID, record: Mapping[str, Any]) -> None:
        path = self._record_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        part.write_text(json.dumps(record, indent=2))
        part.replace(path)

    async def setup(self) -> None:
        (self._container.state / "sessions").mkdir(parents=True, exist_ok=True)

    async def create(self, session_id: uuid.UUID) -> None:
        if self._record_path(session_id).exists():
            raise SessionExistsError(f"session {session_id} exists")
        self._save(session_id, {"cloud_session": "", "url": "", "turns": []})

    async def exists(self, session_id: uuid.UUID) -> bool:
        return self._record_path(session_id).exists()

    async def fork(self, source_id: uuid.UUID, target_id: uuid.UUID, *, checkpoint_id: str) -> None:
        self._load(source_id)
        raise EngineError("a cloud session cannot be forked: start a new conversation")

    async def forget(self, session_id: uuid.UUID) -> None:
        # The cloud session and its tincan branch stay: claude.ai deletes the one, the person
        # the other.
        self._record_path(session_id).unlink(missing_ok=True)

    # --- a turn --------------------------------------------------------------------------------

    def stream(
        self,
        session_id: uuid.UUID,
        agent: AgentDefinition,
        prompt: str,
        *,
        model: str,
        checkpoint_id: str | None,
        timeout_seconds: float,
        resume: bool = False,
        task_payload: Mapping[str, Any] | None = None,
    ) -> AsyncGenerator[Event, None]:
        return self._stream(
            session_id, agent, prompt, model, checkpoint_id, timeout_seconds, resume
        )

    async def _stream(
        self,
        session_id: uuid.UUID,
        agent: AgentDefinition,
        prompt: str,
        model: str,
        checkpoint_id: str | None,
        timeout_seconds: float,
        resume: bool,
    ) -> AsyncGenerator[Event, None]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        record = self._load(session_id)
        if model not in self._settings.models.models:
            raise UnknownModelError(f"no model {model!r}")
        turns = record["turns"]
        answered = [t["checkpoint"] for t in turns if t.get("checkpoint")]
        if checkpoint_id is not None and checkpoint_id not in answered:
            raise CheckpointNotFoundError(f"no checkpoint {checkpoint_id!r} in {session_id}")
        last = turns[-1] if turns else None
        again = resume and last is not None and last["from"] == checkpoint_id
        if not again and checkpoint_id != (answered[-1] if answered else None):
            raise EngineError(
                "a cloud session goes on from its latest reply alone: an edit or a regenerate"
                " of an earlier message needs a new conversation"
            )
        if not self._container.tincan_url:
            raise EngineError(f"the worker has no tincan repository: set {TINCAN_URL}")

        if again:
            turn = last
            if turn.get("answer") is not None:
                yield TextDelta(text=turn["answer"])
                yield Done(text=turn["answer"], checkpoint_id=turn["checkpoint"])
                return
        else:
            turn = {"n": len(turns) + 1, "from": checkpoint_id, "checkpoint": None, "answer": None}
            name = self._settings.models.models[model].name
            await self._send(session_id, record, agent, prompt, turn["n"], name)
            turns.append(turn)
            self._save(session_id, record)

        answer = None
        async for event in self._wait(session_id, record, turn["n"], deadline):
            if isinstance(event, str):
                answer = event
            else:
                yield event
        turn["answer"] = answer
        turn["checkpoint"] = str(uuid.uuid4())
        self._save(session_id, record)
        yield TextDelta(text=answer)
        yield Done(text=answer, checkpoint_id=turn["checkpoint"])

    async def _send(
        self,
        session_id: uuid.UUID,
        record: dict[str, Any],
        agent: AgentDefinition,
        prompt: str,
        n: int,
        model: str,
    ) -> None:
        """Starts the cloud session with the first turn's prompt, or sends it a later one."""
        folder = await self._tincan.ready()
        message = tincan.marker(session_id, n) + "\n" + prompt
        name = tincan.branch(session_id)
        if not record["cloud_session"] and name in await self._tincan.heads():
            # A first turn that failed after the session took its prompt: the session is there.
            found = await self._tincan.read(name, [tincan.SESSION])
            record["cloud_session"] = tincan.parsed(found.get(tincan.SESSION)).get("session", "")
        cli = self._container.cli
        try:
            if record["cloud_session"]:
                sent = await cloud.send(cli, folder, record["cloud_session"], message)
            else:
                if agent.system_prompt.strip():
                    message = f"{message}\n\n<instructions>\n{agent.system_prompt}\n</instructions>"
                sent = await cloud.start(cli, folder, message, model)
        except cloud.CloudError as refused:
            raise EngineError(f"claude --cloud refused the prompt: {refused}") from None
        record["cloud_session"] = record["cloud_session"] or sent.id
        record["url"] = sent.url or record["url"]
        self._poller.kick()

    async def _wait(
        self, session_id: uuid.UUID, record: dict[str, Any], n: int, deadline: float
    ) -> AsyncGenerator[Event | str, None]:
        """The cloud session's progress, as reasoning, then the reply of turn ``n``, as a
        string. ``EngineError`` when the session never takes the prompt, or goes silent."""
        loop = asyncio.get_running_loop()
        name = tincan.branch(session_id)
        files = [tincan.turn_file(n), tincan.STATUS, tincan.SESSION]
        sent = changed = loop.time()
        seen, started, told, state = None, False, False, ""
        cycle = 0
        if record["url"]:
            # The link first: the person follows the session there while the turn waits.
            told = True
            yield ReasoningDelta(text=f"The cloud session: {record['url']}\n")
        async with self._poller.watch() as poller:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError(f"turn {n} of {session_id}: no reply in time")
                cycle, heads = await poller.next(cycle, remaining)
                now = loop.time()
                head = heads.get(name)
                if head and head != seen:
                    seen, changed = head, now
                    found = await self._tincan.read(name, files)
                    session = tincan.parsed(found.get(tincan.SESSION))
                    status = tincan.parsed(found.get(tincan.STATUS))
                    if session.get("session") and not record["cloud_session"]:
                        record["cloud_session"] = session["session"]
                        record["url"] = session.get("url") or record["url"]
                        self._save(session_id, record)
                    answer = found.get(tincan.turn_file(n))
                    if status.get("turn") == n or answer is not None:
                        started = True
                    if started and not told and record["url"]:
                        told = True
                        yield ReasoningDelta(text=f"The cloud session: {record['url']}\n")
                    if answer is not None:
                        yield answer
                        return
                    if started and status.get("state") != state:
                        state = status.get("state", "")
                        yield ReasoningDelta(text=f"The cloud session is {state}.\n")
                why = f" (tincan: {poller.error})" if poller.error else ""
                if not started and now - sent > self._container.start_seconds:
                    raise EngineError(
                        f"the cloud session took no prompt in {self._container.start_seconds:.0f}"
                        f" seconds: check the environment's tincan hooks{why}"
                    )
                if started and now - changed > self._container.silent_seconds:
                    raise EngineError(
                        f"the cloud session has been silent for"
                        f" {self._container.silent_seconds:.0f} seconds{why}"
                    )
