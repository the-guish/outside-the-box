#!/bin/sh
# The claude-cloud engine's tests and the tincan hook's, on real git, outside Docker. They run
# in the environment of Robinauts' Claude Code worker at the commit robinauts.pin names,
# as the Dockerfile installs it, with the engine beside it.
#
#     tests/run.sh                 fetches Robinauts at the pinned commit
#     tests/run.sh <a checkout>    reads Robinauts from that checkout
#
# Needs git, uv and python3.
set -eu

if [ "$#" -gt 1 ]; then
    printf 'usage: %s [a checkout of Robinauts]\n' "$0" >&2
    exit 2
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'rm -rf "$work"; exit 130' INT
trap 'rm -rf "$work"; exit 143' TERM

if [ "$#" -eq 1 ]; then
    robinauts=$(CDPATH= cd -- "$1" && pwd)
else
    pinned=$(cat "$root/robinauts.pin")
    url=${pinned%%#*}
    commit=${pinned#*#}
    if [ -z "$pinned" ] || [ "$url" = "$pinned" ] || [ -z "$commit" ]; then
        printf 'robinauts.pin names no commit of Robinauts\n' >&2
        exit 1
    fi
    robinauts="$work/robinauts"
    git init --quiet "$robinauts"
    git -C "$robinauts" fetch --quiet --depth 1 "$url" "$commit"
    git -C "$robinauts" checkout --quiet --detach FETCH_HEAD
fi

# The worker, as the image's Dockerfile installs it, then the engine without dependencies.
export UV_PROJECT_ENVIRONMENT="$work/venv" PYTHONDONTWRITEBYTECODE=1
(cd "$robinauts/extra-workers/claude-code-worker" && uv sync --quiet --locked --no-dev \
    --no-install-package neorc --no-install-package neorc-core --no-install-package oneberth)
uv pip install --quiet --python "$work/venv/bin/python" --no-deps "$root" pytest \
    iniconfig pluggy packaging
"$work/venv/bin/python" -m pytest -q -p no:cacheprovider --rootdir "$root" \
    "$root/tests"
