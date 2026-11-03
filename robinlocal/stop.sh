#!/usr/bin/env bash
# Stops Robinauts and its database. --reset also deletes the volumes: the database,
# the source and the caches. claude-everywhere keeps running: `docker stop claude-everywhere`.
set -euo pipefail
source "$(dirname "$0")/common.sh"

if [[ "${1:-}" == --reset ]]; then
  docker compose down --volumes
else
  docker compose down
fi
