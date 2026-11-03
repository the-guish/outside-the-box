# Sourced by the scripts here.
cd "$(dirname "${BASH_SOURCE[0]}")"

PORT="${ROBINLOCAL_PORT:-8010}"
CLAUDE_PORT="${CLAUDE_EVERYWHERE_PORT:-8787}"
CLAUDE_DIR="${CLAUDE_EVERYWHERE_DIR:-../claude-everywhere}"

# Waits up to $2 seconds for $1 to answer.
wait_for() {
  local url=$1 seconds=$2
  for ((i = 0; i < seconds; i++)); do
    curl -fs -o /dev/null "$url" && return 0
    sleep 1
  done
  return 1
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
