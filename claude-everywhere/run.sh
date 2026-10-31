#!/usr/bin/env bash
# Builds the image and runs claude-everywhere in Docker, on http://127.0.0.1:$PORT/v1.
# Extra arguments go to `docker run`, for example `./run.sh -d` to run in the background.
set -euo pipefail
cd "$(dirname "$0")"

IMAGE=claude-everywhere
PORT="${CLAUDE_EVERYWHERE_PORT:-8787}"
TOKEN_FILE="${CLAUDE_EVERYWHERE_TOKEN_FILE:-$HOME/.config/claude-everywhere/oauth-token.gpg}"

if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" && -z "${ANTHROPIC_API_KEY:-}" ]]; then
  if [[ -s "$TOKEN_FILE" ]]; then
    vault=read
  else
    vault=create
    echo "No token yet. 'claude setup-token' will sign you in; the token is saved encrypted, not shown."
  fi
  CLAUDE_CODE_OAUTH_TOKEN="$(python3 scripts/token_vault.py "$vault" "$TOKEN_FILE")"
fi

# Passed by name, so values stay out of the process list.
env_args=()
for name in CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY CLAUDE_EVERYWHERE_API_KEY \
    CLAUDE_EVERYWHERE_MODEL CLAUDE_EVERYWHERE_TOOLS CLAUDE_EVERYWHERE_EXTRA_ARGS \
    CLAUDE_EVERYWHERE_MAX_CONCURRENCY CLAUDE_EVERYWHERE_TIMEOUT; do
  if [[ -n "${!name:-}" ]]; then
    export "${name?}"
    env_args+=(-e "$name")
  fi
done

build_args=()
[[ -n "${CLAUDE_VERSION:-}" ]] && build_args+=(--build-arg "CLAUDE_VERSION=$CLAUDE_VERSION")
docker build -q -t "$IMAGE" "${build_args[@]}" . > /dev/null

echo "claude-everywhere on http://127.0.0.1:$PORT/v1"
exec docker run --rm --init --name claude-everywhere \
  -p "127.0.0.1:$PORT:8787" \
  -v claude-everywhere-data:/data \
  "${env_args[@]}" "$@" "$IMAGE"
