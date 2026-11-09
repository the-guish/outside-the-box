#!/usr/bin/env bash
# Runs inside the robinauts container. `fetch` replaces the source with a fresh download
# of the zip; `serve` fetches only when there is no source yet, then builds and starts.
set -euo pipefail

SRC=/work/robinauts
CONFIG=/config/robinauts.toml
SOURCE_URL=/work/source-url
PROJECT=${ROBINLOCAL_PROJECT:-robinlocal}

fetch() {
  echo "Downloading $ROBINLOCAL_ZIP_URL ..."
  rm -rf /work/incoming && mkdir /work/incoming
  curl -fsSL -o /work/incoming/source.zip "$ROBINLOCAL_ZIP_URL"
  unzip -q /work/incoming/source.zip -d /work/incoming
  rm /work/incoming/source.zip
  local new
  new=$(find /work/incoming -mindepth 1 -maxdepth 1 -type d)
  [[ $(wc -l <<<"$new") -eq 1 ]] || { echo "The zip must hold one top directory." >&2; exit 1; }
  # Keep node_modules when the lock did not change, which saves an npm ci.
  if [[ -d $SRC/frontend/node_modules ]] &&
    cmp -s "$SRC/frontend/package-lock.json" "$new/frontend/package-lock.json"; then
    mv "$SRC/frontend/node_modules" "$new/frontend/"
  fi
  rm -rf "$SRC"
  mv "$new" "$SRC"
  rmdir /work/incoming
  printf '%s\n' "$ROBINLOCAL_ZIP_URL" >"$SOURCE_URL"
  echo "Source ready in the work volume."
}

write_config() {
  local tools='tools = ["fetch", "todo", "diagrams"]'
  if [[ ${ROBINLOCAL_CLOUD:-0} == 1 ]]; then
    tools='tools = ["fetch", "todo", "diagrams", "cloud"]'
  fi
  cat >"$CONFIG" <<TOML
[model_providers.claude-everywhere]
kind = "anthropic-compatible"
base_url = "$ROBINLOCAL_CLAUDE_URL"
api_key_env = "ROBINLOCAL_CLAUDE_KEY"

# claude-everywhere passes these aliases to claude --model.
[models.sonnet]
provider = "claude-everywhere"
name = "sonnet"
title = "Claude Sonnet (Claude Code)"
timeout_seconds = 600

[models.opus]
provider = "claude-everywhere"
name = "opus"
title = "Claude Opus (Claude Code)"
timeout_seconds = 600

[models.haiku]
provider = "claude-everywhere"
name = "haiku"
title = "Claude Haiku (Claude Code)"
timeout_seconds = 600

[agents.assistant-langgraph]
title = "Assistant (LangGraph)"
model = "sonnet"
engine = "langchain"
system_prompt = "You are a helpful assistant."
$tools

[agents.assistant-pydantic-ai]
title = "Assistant (Pydantic AI)"
model = "sonnet"
engine = "pydantic-ai"
system_prompt = "You are a helpful assistant."
$tools

# A local MCP server, in its own container.
[tool_servers.fetch]
url = "http://127.0.0.1:$ROBINLOCAL_FETCH_PORT/mcp"
auth = "none"
timeout_seconds = 60

# Robinauts' MCP apps: their views open in the side panel.
[tool_servers.todo]
url = "http://127.0.0.1:$ROBINLOCAL_TODO_PORT/mcp"
auth = "none"
timeout_seconds = 30

[tool_servers.diagrams]
url = "http://127.0.0.1:$ROBINLOCAL_DIAGRAMS_PORT/mcp"
auth = "none"
timeout_seconds = 30
TOML
  if [[ ${ROBINLOCAL_CLOUD:-0} == 1 ]]; then
    # claude-everywhere's start_cloud_session, on when its CLOUD_REPOS names repositories.
    cat >>"$CONFIG" <<TOML

[tool_servers.cloud]
url = "${ROBINLOCAL_CLOUD_URL:-$ROBINLOCAL_CLAUDE_URL/cloud/mcp}"
secret_env = "ROBINLOCAL_CLAUDE_KEY"
timeout_seconds = 180
TOML
  fi
  if [[ -n ${CLAUDE_CODE_OAUTH_TOKEN:-} ]]; then
    cat >>"$CONFIG" <<TOML

# Claude Code in a container, on the subscription behind the token. Anthropic is named for
# this worker alone, and ANTHROPIC_API_KEY stays unset: the worker refuses it.
[model_providers.claude-code]
kind = "anthropic"
api_key_env = "ANTHROPIC_API_KEY"

[models.cc-opus]
provider = "claude-code"
name = "claude-opus-5-5"
title = "Claude Opus 5.5 (Claude Code)"

[agents.claude-code]
title = "Claude Code"
engine = "claude-code"
model = "cc-opus"
worker = "claude"
turn_timeout_seconds = 1800
system_prompt = "You work in this conversation's own directory. Say what you did."

# The agent has Bash, so its container stays off the host's network. It reaches the server
# through the relay on Docker's bridge address, and not the host's loopback.
[workers.claude]
command = [
    "docker", "run", "-i", "--rm", "--name", "$PROJECT-claude",
    "-e", "ROBINAUTS_CONFIG", "-e", "ROBINAUTS_WORKER_NAME",
    "-e", "ROBINAUTS_WORKER_BROKER_URL=http://$gateway:$ROBINLOCAL_PORT",
    "-e", "ROBINAUTS_CLAUDE_AUTH=personal", "-e", "CLAUDE_CODE_OAUTH_TOKEN",
    "-v", "${PROJECT}_config:/config:ro",
    "-v", "$PROJECT-claude-sessions:/var/lib/claude",
    "-v", "$PROJECT-claude-workspaces:/workspace",
    "$PROJECT-claude-worker",
]
TOML
  fi
}

# Installs one of the source's MCP apps and starts it on loopback, beside the server.
start_mcp_app() {
  local folder=$1 command=$2 port=$3
  shift 3
  local env=/cache/mcp/$folder
  (cd "$SRC/mcp/$folder" && UV_PROJECT_ENVIRONMENT=$env uv sync --locked --no-dev --python 3.12 --no-editable --quiet)
  "$env/bin/$command" --host 127.0.0.1 --port "$port" "$@" &
  for _ in $(seq 60); do
    (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && return 0
    sleep 0.5
  done
  echo "$folder did not listen on port $port." >&2
  exit 1
}

# The image, and the relay the worker's container reaches the server through. The server
# binds loopback alone, and the worker is on Docker's bridge.
prepare_worker() {
  gateway=$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')
  echo "Building the Claude Code worker's image ..."
  docker build --quiet -f "$SRC/extra-workers/claude-code-worker/Dockerfile" \
    -t "$PROJECT-claude-worker" "$SRC" >/dev/null
  docker rm -f "$PROJECT-claude" >/dev/null 2>&1 || true
  socat "TCP-LISTEN:$ROBINLOCAL_PORT,bind=$gateway,fork,reuseaddr" "TCP:127.0.0.1:$ROBINLOCAL_PORT" &
}

serve() {
  # Another URL means other source, so a changed setting downloads it again.
  if [[ ! -d $SRC || $(cat "$SOURCE_URL" 2>/dev/null) != "$ROBINLOCAL_ZIP_URL" ]]; then
    fetch
  fi
  if [[ ! -f $SRC/frontend/dist/index.html ]]; then
    cd "$SRC/frontend"
    [[ -d node_modules ]] || npm ci --ignore-scripts
    npm run build
  fi
  cd "$SRC/backend"
  uv sync --locked --no-dev --python 3.12 --no-editable --reinstall-package robinauts --quiet
  start_mcp_app to-do-list robinauts-to-do-list "$ROBINLOCAL_TODO_PORT" --file /work/todos.json
  start_mcp_app diagrams robinauts-diagrams "$ROBINLOCAL_DIAGRAMS_PORT"
  if [[ -n ${CLAUDE_CODE_OAUTH_TOKEN:-} ]]; then
    prepare_worker
  fi
  write_config
  export ROBINAUTS_CONFIG=$CONFIG
  /venv/bin/robinauts db init
  exec /venv/bin/robinauts start --dev-no-sign-in --port "$ROBINLOCAL_PORT"
}

case "${1:-}" in
fetch) fetch ;;
serve) serve ;;
*)
  echo "usage: entry.sh fetch|serve" >&2
  exit 2
  ;;
esac
