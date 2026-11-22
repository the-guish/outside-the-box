"""The local side of tincan: a clone of the tincan repository, and the poller every waiting
turn shares.

A conversation's branch is ``s/<conversation id>``. The cloud session's hooks write it
(``environment/setup.sh``); this side reads it alone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

SESSION = "session.json"
STATUS = "status.json"
GIT_SECONDS = 120.0


class TincanError(Exception):
    """git refused, or took longer than ``GIT_SECONDS``."""


def branch(conversation: uuid.UUID) -> str:
    return f"s/{conversation}"


def turn_file(turn: int) -> str:
    return f"turns/{turn:04d}.md"


def marker(conversation: uuid.UUID, turn: int) -> str:
    """The line a prompt opens with: the hook reads the conversation and the turn from it."""
    return f"[tincan {conversation} turn {turn}]"


class Tincan:
    """A clone of the tincan repository at ``path``. One git command runs at a time."""

    def __init__(self, url: str, path: Path) -> None:
        self.url = url
        self.path = path
        self._lock = asyncio.Lock()

    async def _git(self, *args: str, cwd: Path | None = None) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), GIT_SECONDS)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise TincanError(f"git {args[0]}: no answer in {GIT_SECONDS:.0f} seconds") from None
        if process.returncode != 0:
            raise TincanError(f"git {args[0]}: {err.decode(errors='replace').strip()}")
        return out.decode()

    async def ready(self) -> Path:
        """The clone, made at the first call: the folder ``claude --cloud`` starts from."""
        async with self._lock:
            if not (self.path / ".git").is_dir():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                await self._git("clone", "--quiet", self.url, str(self.path))
            return self.path

    async def heads(self) -> dict[str, str]:
        """Every conversation's branch and its commit, in one request to the remote."""
        out = await self._git("ls-remote", "--heads", self.url, "refs/heads/s/*")
        heads = {}
        for line in out.splitlines():
            sha, _, ref = line.partition("\t")
            heads[ref.removeprefix("refs/heads/")] = sha
        return heads

    async def read(self, name: str, files: list[str]) -> dict[str, str]:
        """The files of branch ``name`` that it holds, fetched anew."""
        await self.ready()
        async with self._lock:
            ref = f"refs/remotes/origin/{name}"
            await self._git(
                "fetch", "--quiet", "origin", f"+refs/heads/{name}:{ref}", cwd=self.path
            )
            found = {}
            for file in files:
                try:
                    found[file] = await self._git("show", f"{ref}:{file}", cwd=self.path)
                except TincanError:
                    continue
            return found


def parsed(text: str | None) -> Mapping[str, Any]:
    try:
        value = json.loads(text or "")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


class Poller:
    """One ``git ls-remote`` per cycle, for every waiting turn. It runs while a turn waits.

    The interval starts at ``base`` and grows by half at each cycle that sees no change, up to
    ``most``. A change, or a new turn's ``kick``, sets it back to ``base``.
    """

    def __init__(self, tincan: Tincan, base: float, most: float) -> None:
        self._tincan = tincan
        self._base = base
        self._most = most
        self._interval = base
        self._heads: dict[str, str] = {}
        self._cycle = 0
        self._error = ""
        self._waiting = 0
        self._published = asyncio.Condition()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def error(self) -> str:
        """The last cycle's failure, or nothing."""
        return self._error

    def kick(self) -> None:
        self._interval = self._base
        self._wake.set()

    def watch(self) -> _Watch:
        return _Watch(self)

    async def next(self, after: int, timeout: float) -> tuple[int, dict[str, str]]:
        """The first cycle past ``after``, and the heads it read. ``TimeoutError`` past
        ``timeout`` seconds."""
        async with self._published:
            await asyncio.wait_for(
                self._published.wait_for(lambda: self._cycle > after), max(timeout, 0)
            )
            return self._cycle, dict(self._heads)

    async def _run(self) -> None:
        while self._waiting:
            try:
                heads = await self._tincan.heads()
                self._error = ""
            except TincanError as failed:
                _log.warning("tincan: %s", failed)
                heads, self._error = self._heads, str(failed)
            if heads != self._heads:
                self._interval = self._base
            else:
                self._interval = min(self._interval * 1.5, self._most)
            async with self._published:
                self._heads = heads
                self._cycle += 1
                self._published.notify_all()
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), self._interval)
            except TimeoutError:
                pass
        self._task = None


class _Watch:
    def __init__(self, poller: Poller) -> None:
        self._poller = poller

    async def __aenter__(self) -> Poller:
        poller = self._poller
        poller._waiting += 1
        if poller._task is None:
            poller._task = asyncio.get_running_loop().create_task(poller._run())
        return poller

    async def __aexit__(self, *_: object) -> None:
        poller = self._poller
        poller._waiting -= 1
        if not poller._waiting and poller._task is not None:
            poller._wake.set()
