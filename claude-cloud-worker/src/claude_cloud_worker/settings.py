"""The engine's settings, read from the worker's environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

TINCAN_URL = "CLAUDE_CLOUD_TINCAN_URL"
POLL_SECONDS = "CLAUDE_CLOUD_POLL_SECONDS"
POLL_MOST_SECONDS = "CLAUDE_CLOUD_POLL_MOST_SECONDS"
START_SECONDS = "CLAUDE_CLOUD_START_SECONDS"
SILENT_SECONDS = "CLAUDE_CLOUD_SILENT_SECONDS"
STATE_DIR = "CLAUDE_CLOUD_STATE"
CLI_PATH = "ROBINAUTS_CLAUDE_CLI_PATH"


class SettingsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Settings:
    tincan_url: str
    """The tincan repository; empty, every turn is refused with what to set."""
    state: Path
    """The sessions' records and the clone of tincan."""
    cli: str
    poll_seconds: float = 20.0
    poll_most_seconds: float = 120.0
    start_seconds: float = 600.0
    """How long a turn waits for the cloud session to take its prompt."""
    silent_seconds: float = 900.0
    """How long a running turn may go without a commit on its branch: three missed beats."""


def bundled_cli() -> str:
    """The Claude Code binary the Agent SDK bundles, which the claude-code engine runs too."""
    try:
        import claude_agent_sdk
    except ImportError:
        return "claude"
    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    return str(bundled) if bundled.is_file() else "claude"


def settings_from(environ: Mapping[str, str] = os.environ) -> Settings:
    def seconds(name: str, default: float) -> float:
        given = environ.get(name, "").strip()
        if not given:
            return default
        try:
            value = float(given)
        except ValueError:
            raise SettingsError(f"{name}: a number of seconds, not {given!r}") from None
        if value <= 0:
            raise SettingsError(f"{name}: a positive number of seconds, not {given!r}")
        return value

    config = Path(environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
    return Settings(
        tincan_url=environ.get(TINCAN_URL, "").strip(),
        state=Path(environ.get(STATE_DIR) or config / "cloud"),
        cli=environ.get(CLI_PATH, "").strip() or bundled_cli(),
        poll_seconds=seconds(POLL_SECONDS, 20.0),
        poll_most_seconds=seconds(POLL_MOST_SECONDS, 120.0),
        start_seconds=seconds(START_SECONDS, 600.0),
        silent_seconds=seconds(SILENT_SECONDS, 900.0),
    )
