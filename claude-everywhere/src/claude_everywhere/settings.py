from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

PREFIX = "CLAUDE_EVERYWHERE_"
DATA_DIR = Path.home() / ".local" / "share" / "claude-everywhere"


def _env(name: str, default: str) -> str:
    return os.environ.get(PREFIX + name, default)


@dataclass(frozen=True)
class Settings:
    claude_bin: str
    workdir: Path
    db_path: Path
    tools: list[str]
    server_tools: tuple[str, ...]
    default_model: str | None
    extra_args: list[str]
    api_key: str | None
    max_concurrency: int
    timeout: float
    tool_timeout: float
    host: str
    port: int

    @classmethod
    def from_env(cls) -> Settings:
        tools = _env("TOOLS", "")
        return cls(
            claude_bin=_env("CLAUDE_BIN", "claude"),
            workdir=Path(_env("WORKDIR", str(DATA_DIR / "workdir"))).expanduser(),
            db_path=Path(_env("DB", str(DATA_DIR / "sessions.db"))).expanduser(),
            tools=[t.strip() for t in tools.split(",") if t.strip()] or [""],
            server_tools=tuple(t.strip() for t in _env("SERVER_TOOLS", "").split(",") if t.strip()),
            default_model=_env("MODEL", "") or None,
            extra_args=shlex.split(_env("EXTRA_ARGS", "")),
            api_key=_env("API_KEY", "") or None,
            max_concurrency=int(_env("MAX_CONCURRENCY", "4")),
            timeout=float(_env("TIMEOUT", "600")),
            tool_timeout=float(_env("TOOL_TIMEOUT", "600")),
            host=_env("HOST", "127.0.0.1"),
            port=int(_env("PORT", "8787")),
        )
