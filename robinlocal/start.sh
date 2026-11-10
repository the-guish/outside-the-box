#!/usr/bin/env bash
# Starts claude-everywhere when it is not answering, then Robinauts and its database.
# The first start downloads the zip; ./refresh.sh downloads it again.
set -euo pipefail
source "$(dirname "$0")/common.sh"

if ! curl -fs -o /dev/null "http://127.0.0.1:$CLAUDE_PORT/v1/models"; then
  # run.sh builds with -q, which hides why a build failed. Built here first, its build is cached.
  build_args=()
  [[ -n "${CLAUDE_VERSION:-}" ]] && build_args+=(--build-arg "CLAUDE_VERSION=$CLAUDE_VERSION")
  docker build -t claude-everywhere "${build_args[@]}" "$CLAUDE_DIR"
  echo "Starting claude-everywhere from $CLAUDE_DIR ..."
  [[ ${ROBINLOCAL_CLOUD:-1} == 1 ]] &&
    export CLAUDE_EVERYWHERE_CLOUD_REPOS="${CLAUDE_EVERYWHERE_CLOUD_REPOS:-the-guish/robinauts}"
  # Without a sign-in, run.sh stops and prints the command that signs in.
  "$CLAUDE_DIR/run.sh" -d
  wait_for "http://127.0.0.1:$CLAUDE_PORT/v1/models" 60 ||
    { echo "claude-everywhere did not answer on port $CLAUDE_PORT." >&2; exit 1; }
fi
echo "claude-everywhere on http://127.0.0.1:$CLAUDE_PORT"
check_cloud

if [[ -z "${ROBINLOCAL_CLAUDE_CODE_TOKEN:-}" ]]; then
  echo "No ROBINLOCAL_CLAUDE_CODE_TOKEN, so there is no Claude Code agent. See README.md."
fi
docker compose up -d --build
wait_for_robinauts
