#!/usr/bin/env bash
# Stops Robinauts and its database. --reset also deletes the volumes: the database,
# the source, the caches and the Claude Code worker's conversations. claude-everywhere keeps running: `docker stop claude-everywhere`.
set -euo pipefail
source "$(dirname "$0")/common.sh"

if [[ "${1:-}" == --reset ]]; then
  docker compose down --volumes
  docker rm -f "$PROJECT-claude" >/dev/null 2>&1 || true
  docker volume rm -f "$PROJECT-claude-sessions" "$PROJECT-claude-workspaces" >/dev/null
else
  docker compose down
  # The server stops its worker; this is for one that outlived a killed server.
  docker rm -f "$PROJECT-claude" >/dev/null 2>&1 || true
fi
