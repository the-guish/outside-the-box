#!/usr/bin/env bash
# Starts claude-everywhere when it is not answering, then Robinauts and its database.
# The first start downloads the zip; ./refresh.sh downloads it again.
set -euo pipefail
source "$(dirname "$0")/common.sh"

TOKEN_FILE="${CLAUDE_EVERYWHERE_TOKEN_FILE:-$HOME/.config/claude-everywhere/oauth-token.gpg}"

if ! curl -fs -o /dev/null "http://127.0.0.1:$CLAUDE_PORT/v1/models"; then
  # Creating a token opens an interactive sign-in, so this script only uses one that exists.
  if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}${ANTHROPIC_API_KEY:-}" && ! -s "$TOKEN_FILE" ]]; then
    echo "claude-everywhere is not running on port $CLAUDE_PORT, and it has no token yet." >&2
    echo "Run $CLAUDE_DIR/run.sh once in a terminal of its own to create one," >&2
    echo "or set CLAUDE_CODE_OAUTH_TOKEN. Then run ./start.sh again." >&2
    exit 1
  fi
  # run.sh builds with -q, which hides why a build failed. Built here first, its build is cached.
  build_args=()
  [[ -n "${CLAUDE_VERSION:-}" ]] && build_args+=(--build-arg "CLAUDE_VERSION=$CLAUDE_VERSION")
  docker build -t claude-everywhere "${build_args[@]}" "$CLAUDE_DIR"
  echo "Starting claude-everywhere from $CLAUDE_DIR (it asks for the token password) ..."
  "$CLAUDE_DIR/run.sh" -d
  wait_for "http://127.0.0.1:$CLAUDE_PORT/v1/models" 60 ||
    { echo "claude-everywhere did not answer on port $CLAUDE_PORT." >&2; exit 1; }
fi
echo "claude-everywhere on http://127.0.0.1:$CLAUDE_PORT"

docker compose up -d --build
wait_for_robinauts
