"""The worker's process, ``python -m claude_cloud_worker``: Robinauts' Claude Code worker,
which also runs the agents on ``claude-cloud``.

Robinauts' worker refuses an agent on any engine but ``claude-code``, since its credential
checks cover that engine alone. ``claude-cloud`` runs the same Claude Code binary, with the
full sign-in the worker's volume holds (``cloud.py``). This module widens that one check, then
runs Robinauts' worker as it is.
"""

from __future__ import annotations

import sys

from robinauts.shared_domain.domain import Config
from robinauts_claude_worker import __main__ as claude_worker

from claude_cloud_worker.engine import ENGINE

ENGINES = (claude_worker.engine.ENGINE, ENGINE)


def configuration_problems(name: str, config: Config) -> list[str]:
    """Robinauts' check, with both engines allowed."""
    problems = []
    if name not in config.workers():
        problems.append(f"no agent of the configuration names worker {name!r}")
    for agent in config.for_worker(name).agents.values():
        if agent.engine not in ENGINES:
            problems.append(
                f"agent {agent.id!r} runs on engine {agent.engine!r}: this worker runs"
                f" {' and '.join(ENGINES)}"
            )
    return problems


def main() -> int:
    claude_worker.configuration_problems = configuration_problems
    return claude_worker.main()


if __name__ == "__main__":
    sys.exit(main())
