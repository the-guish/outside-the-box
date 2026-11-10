#!/usr/bin/env bash
# Stops what start.sh beside it started: the chat server, then PostgreSQL and
# the tools (compose.yaml). The conversations stay in the database's volume.
#
# Usage: stop.sh [--reset]    --reset also deletes the volumes: every
#                             conversation, the to-do list, and the Claude Code
#                             worker's transcripts and workspaces.

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
reset=no
case "${1:-}" in
"") ;;
--reset) reset=yes ;;
*) echo "usage: stop.sh [--reset]" >&2; exit 2 ;;
esac

docker_() {
    if docker info >/dev/null 2>&1; then
        docker "$@"
    else
        sg docker -c "$(printf '%q ' docker "$@")"
    fi
}

# compose.yaml refuses to run without these. `down` builds and mounts nothing,
# so the paths need not exist.
export DEMO_FILES_DIR=${EXTENDED_FILES_DIR:-$HOME/agents-fs}
export DEMO_REPO="$HOME/code/robinfork/robinauts"

# The chat server, by the pid start.sh wrote, and only while that pid is still
# this example's robinauts, not a number handed out again.
pid_file="$here/.state/server.pid"
if [ -f "$pid_file" ]; then
    pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null &&
        tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q "$here/.state/venv/bin/"; then
        echo "Stopping the chat server (pid $pid) ..."
        kill "$pid"
        for _ in $(seq 150); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.2
        done
        kill -9 "$pid" 2>/dev/null || true
    else
        echo "No chat server was running."
    fi
    rm -f "$pid_file"
else
    echo "No chat server was running."
fi

# The Claude Code worker stops with the server. This is for one that outlived it.
docker_ rm -f robinauts-extended-claude >/dev/null 2>&1 || true

if [ "$reset" = yes ]; then
    echo "Stopping PostgreSQL and the tools, and deleting their volumes ..."
    docker_ compose -f "$here/compose.yaml" --profile claude down --volumes
    rm -f "$here/.state/pg-password"
    echo "Deleting the Claude Code worker's transcripts and workspaces ..."
    docker_ volume rm -f robinauts-extended-claude-sessions robinauts-extended-claude-workspaces >/dev/null
else
    echo "Stopping PostgreSQL and the tools (the conversations stay) ..."
    docker_ compose -f "$here/compose.yaml" --profile claude down
fi
