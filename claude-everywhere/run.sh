#!/usr/bin/env bash
# Builds the image and runs claude-everywhere in Docker, on http://127.0.0.1:$PORT/v1.
# Extra arguments go to `docker run`, for example `./run.sh -d` to run in the background.
set -euo pipefail
cd "$(dirname "$0")"

IMAGE=claude-everywhere
PORT="${CLAUDE_EVERYWHERE_PORT:-8787}"
VOLUME=claude-everywhere-data

# Passed by name, so values stay out of the process list.
env_args=()
for name in CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY CLAUDE_EVERYWHERE_API_KEY \
    CLAUDE_EVERYWHERE_MODEL CLAUDE_EVERYWHERE_TOOLS CLAUDE_EVERYWHERE_EXTRA_ARGS \
    CLAUDE_EVERYWHERE_SERVER_TOOLS CLAUDE_EVERYWHERE_MAX_CONCURRENCY CLAUDE_EVERYWHERE_TIMEOUT \
    CLAUDE_EVERYWHERE_TOOL_TIMEOUT CLAUDE_EVERYWHERE_CLOUD_REPOS CLAUDE_EVERYWHERE_GITHUB_TOKEN; do
  if [[ -n "${!name:-}" ]]; then
    export "${name?}"
    env_args+=(-e "$name")
  fi
done

build_args=()
[[ -n "${CLAUDE_VERSION:-}" ]] && build_args+=(--build-arg "CLAUDE_VERSION=$CLAUDE_VERSION")
docker build -t "$IMAGE" "${build_args[@]}" .

# claude signs in with the /login kept in the volume, unless a token or key is set.
if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}${ANTHROPIC_API_KEY:-}" ]] &&
  ! docker run --rm -v "$VOLUME:/data" "$IMAGE" test -s /data/claude/.credentials.json; then
  echo "claude-everywhere has no sign-in yet. Sign in once, in a terminal:" >&2
  echo "  docker run --rm -it -v $VOLUME:/data $IMAGE claude /login" >&2
  exit 1
fi

echo "claude-everywhere on http://127.0.0.1:$PORT/v1"
exec docker run --rm --init --name claude-everywhere \
  -p "127.0.0.1:$PORT:8787" \
  -v "$VOLUME:/data" \
  "${env_args[@]}" "$@" "$IMAGE"
