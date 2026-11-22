"""Keeps the worker signed in with the sign-in of a claude that runs elsewhere.

    python -m claude_cloud_worker.sign_in <its .credentials.json> <the worker's .credentials.json>

The copy has no refresh token: a refresh by the worker would end the sign-in of the other
claude. That claude alone refreshes it, and this process copies each new one.

The other claude replaces its file at each refresh, so its folder is what a container mounts.
The process may run as root, to read a folder the worker's user cannot: the copy then gets the
owner of the folder it is in.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

EVERY_SECONDS = 30.0


class NoSignIn(Exception):
    pass


def token_of(path: Path) -> str | None:
    try:
        return json.loads(path.read_text())["claudeAiOauth"]["accessToken"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def copy(source: Path, target: Path) -> bool:
    """Copies the sign-in of ``source`` to ``target``. False when ``target`` holds it already."""
    try:
        signed = dict(json.loads(source.read_text())["claudeAiOauth"])
        token = signed["accessToken"]
    except (OSError, ValueError, KeyError, TypeError):
        raise NoSignIn(f"{source} holds no sign-in of claude.ai: run claude /login beside it") from None
    if token_of(target) == token:
        return False
    signed.pop("refreshToken", None)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    part.touch(0o600)
    part.write_text(json.dumps({"claudeAiOauth": signed}))
    if os.geteuid() == 0:
        folder = target.parent.stat()
        os.chown(part, folder.st_uid, folder.st_gid)
    part.replace(target)
    return True


def main(args: list[str]) -> int:
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    source, target = Path(args[0]), Path(args[1])
    said = None
    while True:
        try:
            say = "copied a new sign-in" if copy(source, target) else said
        except NoSignIn as missing:
            say = str(missing)
        if say != said or say == "copied a new sign-in":
            print(say, flush=True)
        said = say if say != "copied a new sign-in" else None
        time.sleep(EVERY_SECONDS)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
