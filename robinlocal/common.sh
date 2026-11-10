# Sourced by the scripts here.
cd "$(dirname "${BASH_SOURCE[0]}")"

PORT="${ROBINLOCAL_PORT:-8010}"
CLAUDE_PORT="${CLAUDE_EVERYWHERE_PORT:-8787}"
CLAUDE_DIR="${CLAUDE_EVERYWHERE_DIR:-../claude-everywhere}"
PROJECT="${COMPOSE_PROJECT_NAME:-robinlocal}"

# Compose reads .env by itself, but this script needs the token to choose the files.
if [[ -z "${ROBINLOCAL_CLAUDE_CODE_TOKEN:-}" && -f .env ]]; then
  ROBINLOCAL_CLAUDE_CODE_TOKEN=$(sed -n 's/^ROBINLOCAL_CLAUDE_CODE_TOKEN=//p' .env)
  export ROBINLOCAL_CLAUDE_CODE_TOKEN
fi
if [[ -n "${ROBINLOCAL_CLAUDE_CODE_TOKEN:-}" ]]; then
  export COMPOSE_FILE=compose.yaml:compose.worker.yaml
  ROBINLOCAL_DOCKER_GID=$(stat -c %g /var/run/docker.sock)
  export ROBINLOCAL_DOCKER_GID
fi

# Waits up to $2 seconds for $1 to answer.
wait_for() {
  local url=$1 seconds=$2
  for ((i = 0; i < seconds; i++)); do
    curl -fs -o /dev/null "$url" && return 0
    sleep 1
  done
  return 1
}

# Turns the cloud tool off when claude-everywhere answers without its cloud endpoint.
check_cloud() {
  [[ ${ROBINLOCAL_CLOUD:-1} == 1 ]] || return 0
  local status
  status=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$CLAUDE_PORT/cloud/mcp" \
    -H "Authorization: Bearer ${CLAUDE_EVERYWHERE_API_KEY:-unused}" -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"ping"}')
  if [[ $status != 200 ]]; then
    echo "claude-everywhere has no cloud sessions (HTTP $status), so the agents run without them." >&2
    echo "Restart it with CLAUDE_EVERYWHERE_CLOUD_REPOS set to turn them on." >&2
    export ROBINLOCAL_CLOUD=0
  fi
}

wait_for_robinauts() {
  echo "Waiting for http://127.0.0.1:$PORT/ (the first start builds the interface) ..."
  if ! wait_for "http://127.0.0.1:$PORT/health" 900; then
    docker compose logs --tail 40 robinauts >&2
    echo "Robinauts did not come up; the log is above." >&2
    exit 1
  fi
  echo "Robinauts is up: http://127.0.0.1:$PORT/"
  echo "  log: docker compose logs -f robinauts   stop: ./stop.sh"
}
