"""The PreToolUse hook of the run that reads a cloud session: exit 2 refuses a tool call."""

import json
import os
import sys

from .cloud import ASKED_VARIABLE, refused_call


def main() -> None:
    reason = refused_call(json.load(sys.stdin), os.environ[ASKED_VARIABLE])
    if reason:
        print(reason, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
