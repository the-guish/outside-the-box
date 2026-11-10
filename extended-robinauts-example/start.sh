#!/usr/bin/env bash
# The extended Robinauts example: the platform with five MCP tool servers,
# neorc's flows and, optionally, the Claude Code worker, on this machine's
# loopback alone (README.md).
#
#   robinauts.toml         the agents' configuration: models, agents, tools
#   claude-worker.toml.in  the Claude Code worker's tables, appended when
#                          CLAUDE_CODE_OAUTH_TOKEN is set
#   compose.yaml           PostgreSQL, the tools and the worker's relay
#   .state/                the Python environment, the configuration written,
#                          the database password, the files tool's key, the
#                          flows folder, the pid
#   ~/agents-fs/           the one folder the files tool reads and writes
#                          (EXTENDED_FILES_DIR)
# From the repo it takes the source: the backend, installed from its lock into
# .state/venv, the interface, built afresh at every start, the to-do list and
# diagrams servers, the flows with their handlers, and the worker's image.
#
# Needs: Docker with compose, uv, Node.js (nvm is fine), and
#        OPENROUTER_API_KEY in the environment or in .env beside this script.
#        For the Claude Code agent: CLAUDE_CODE_OAUTH_TOKEN (run
#        `claude setup-token` yourself), and optionally WORKER_GITHUB_TOKEN,
#        a fine-grained token for the repositories it works on.
# Usage: start.sh [path-to-robinauts-repo]   (default ~/code/robinfork/robinauts)
# Env:   ROBINAUTS_DEMO_PORT (8000), EXTENDED_FILES_DIR (~/agents-fs),
#        EXTENDED_REPO_URL (https://github.com/the-guish/robinauts) and
#        EXTENDED_REPO_BRANCH (main): the repository the Claude Code flows offer
# Stop:  Ctrl-C (the chat server only), or stop.sh (everything).

set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=${1:-$HOME/code/robinfork/robinauts}
state="$here/.state"
venv="$state/venv"
port=${ROBINAUTS_DEMO_PORT:-8000}

docker_() {
    if docker info >/dev/null 2>&1; then
        docker "$@"
    else
        sg docker -c "$(printf '%q ' docker "$@")"
    fi
}

if [ ! -d "$repo/backend" ] || [ ! -d "$repo/mcp/to-do-list" ]; then
    echo "$repo is not a Robinauts checkout with mcp/: pass its path as the first argument." >&2
    exit 2
fi
if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    echo "Something already listens on port $port: stop it first, or set ROBINAUTS_DEMO_PORT." >&2
    exit 2
fi

mkdir -p "$state"
chmod 700 "$state"

# The keys: from the environment, or from .env. .env is read, not sourced, and
# only when it is mode 0600, since it holds keys.
from_env_file() {
    [ -z "${!1:-}" ] && [ -f "$here/.env" ] || return 0
    local mode
    mode=$(stat -c %a "$here/.env" 2>/dev/null || stat -f %Lp "$here/.env")
    if [ "$mode" != 600 ]; then
        echo "$here/.env is mode $mode and may hold keys: chmod 600 it." >&2
        exit 2
    fi
    local value
    value=$(sed -n "s/^$1=//p" "$here/.env" | tail -1)
    # One pair of quotes around the value is the file's, not the key's.
    case "$value" in
    \"*\" | \'*\') value=${value:1:${#value}-2} ;;
    esac
    printf -v "$1" '%s' "$value"
}
from_env_file OPENROUTER_API_KEY
from_env_file CLAUDE_CODE_OAUTH_TOKEN
from_env_file WORKER_GITHUB_TOKEN
: "${OPENROUTER_API_KEY:?is not set: export it, or put OPENROUTER_API_KEY=... in $here/.env}"
export OPENROUTER_API_KEY

# The Claude Code worker, when its token is set.
claude=no
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    claude=yes
    export CLAUDE_CODE_OAUTH_TOKEN
    # The worker refuses a key beside the subscription token.
    unset ANTHROPIC_API_KEY
    if [ -n "${WORKER_GITHUB_TOKEN:-}" ]; then
        export GITHUB_TOKEN="$WORKER_GITHUB_TOKEN"
    else
        unset GITHUB_TOKEN
    fi
    # The agent commits as the developer.
    GIT_AUTHOR_NAME=$(git config user.name || true)
    GIT_AUTHOR_EMAIL=$(git config user.email || true)
    if [ -z "$GIT_AUTHOR_NAME" ] || [ -z "$GIT_AUTHOR_EMAIL" ]; then
        echo "git config user.name and user.email must be set: the Claude Code agent commits as you." >&2
        exit 2
    fi
    GIT_COMMITTER_NAME=$GIT_AUTHOR_NAME
    GIT_COMMITTER_EMAIL=$GIT_AUTHOR_EMAIL
    export GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
    # The relay listens on the default bridge's address, for its subnet alone.
    DEMO_BRIDGE_IP=$(docker_ network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')
    DEMO_BRIDGE_SUBNET=$(docker_ network inspect bridge --format '{{(index .IPAM.Config 0).Subnet}}')
    DEMO_PORT=$port
    export DEMO_BRIDGE_IP DEMO_BRIDGE_SUBNET DEMO_PORT
    export COMPOSE_PROFILES=claude
fi

# 1. The database password, made once. Only the first start of an empty
#    volume reads it; after that it is what the database already has.
if [ ! -s "$state/pg-password" ]; then
    (umask 077 && python3 -c 'import secrets; print(secrets.token_urlsafe(24))' >"$state/pg-password")
fi
DEMO_PG_PASSWORD=$(cat "$state/pg-password")
export DEMO_PG_PASSWORD

# The files tool's key, made once: the bearer the server sends and supergateway
# requires. The folder it serves is made here, so that it is the user's and not
# root's, which is what Docker would make of a missing one.
if [ ! -s "$state/files-key" ]; then
    (umask 077 && python3 -c 'import secrets; print(secrets.token_urlsafe(32))' >"$state/files-key")
fi
DEMO_FILES_KEY=$(cat "$state/files-key")
DEMO_UID=$(id -u)
DEMO_GID=$(id -g)
DEMO_FILES_DIR=${EXTENDED_FILES_DIR:-$HOME/agents-fs}
mkdir -p "$DEMO_FILES_DIR"
DEMO_FILES_DIR=$(cd "$DEMO_FILES_DIR" && pwd)
DEMO_REPO=$(cd "$repo" && pwd)
export DEMO_FILES_KEY DEMO_UID DEMO_GID DEMO_FILES_DIR DEMO_REPO

# 2. PostgreSQL, the tools, and the worker's relay when it is on.
echo "Starting PostgreSQL and the tools (docker compose) ..."
docker_ compose -f "$here/compose.yaml" up -d --build --wait --remove-orphans

# 3. The interface, built, and the backend installed from its lock,
#    non-editably, so that the built interface is inside the package the
#    server imports. Both every start, so the example runs the source as it is.
: "${NVM_DIR:=$HOME/.nvm}"
if [ -s "$NVM_DIR/nvm.sh" ]; then
    set +eu
    # shellcheck source=/dev/null
    . "$NVM_DIR/nvm.sh" >/dev/null 2>&1
    nvm use "$(cat "$repo/frontend/.nvmrc")" >/dev/null 2>&1
    set -eu
fi
echo "Building the interface (npm ci, npm run build) ..."
(cd "$repo/frontend" && npm ci --silent && npm run build --silent >/dev/null)
echo "Installing the backend into $venv ..."
(cd "$repo/backend" && UV_PROJECT_ENVIRONMENT="$venv" uv sync --locked --no-dev \
    --python 3.12 --no-editable --reinstall-package robinauts --quiet)
# The worker and the server must be the same build of robinauts.
if [ "$claude" = yes ]; then
    echo "Building the Claude Code worker's image ..."
    docker_ build --quiet -f "$DEMO_REPO/extra-workers/claude-code-worker/Dockerfile" \
        -t robinauts-extended-claude-code-worker "$DEMO_REPO" >/dev/null
fi

# 4. The flows folder: the repo's flows, as they are now, and their settings.
#    code_location names the checkout's handlers: the server is installed
#    non-editably, so its own default would point inside the venv.
#    code_and_review needs the Claude Code agent, so it comes with the worker,
#    with the repository its form offers.
flows="$state/flows"
rm -rf "$flows"
mkdir -p "$flows"
cp "$DEMO_REPO/orchestration/flows/guess_number.yaml" "$flows/"
printf 'code_location = "%s/orchestration"\n' "$DEMO_REPO" >"$flows/flows.toml"
if [ "$claude" = yes ]; then
    cp "$DEMO_REPO"/orchestration/flows/code_and_review.yaml \
        "$DEMO_REPO"/orchestration/flows/code_and_review.form.toml "$flows/"
    repo_url=${EXTENDED_REPO_URL:-https://github.com/the-guish/robinauts}
    repo_id=$(basename "$repo_url" .git)
    printf '\n[repositories.%s]\nurl = "%s"\nbranches = ["%s"]\n' \
        "$repo_id" "$repo_url" "${EXTENDED_REPO_BRANCH:-main}" >>"$flows/flows.toml"
fi
export ROBINAUTS_FLOWS_DIR="$flows"

# 5. The configuration: robinauts.toml, and the worker's tables when it is on.
config="$state/robinauts.toml"
cp "$here/robinauts.toml" "$config"
if [ "$claude" = yes ]; then
    sed -e "s|@STATE@|$state|g" -e "s|@BROKER_URL@|http://$DEMO_BRIDGE_IP:$port|g" \
        "$here/claude-worker.toml.in" >>"$config"
fi
export ROBINAUTS_CONFIG="$config"
export ROBINAUTS_DATABASE_URL="postgresql://robinauts:$DEMO_PG_PASSWORD@127.0.0.1:54392/robinauts"

# 6. The schema. `db init` is idempotent.
"$venv/bin/robinauts" db init

echo
echo "Serving on http://127.0.0.1:$port/"
echo "Agents: Researcher (Sonnet) and Pi Sidekick (Opus), with fetch, browser,"
echo "  files ($DEMO_FILES_DIR), the to-do list and diagrams; and Echo."
if [ "$claude" = yes ]; then
    echo "Claude Code runs in a container, on your subscription, with Bash in its workspace."
    [ -n "${GITHUB_TOKEN:-}" ] || echo "  No WORKER_GITHUB_TOKEN: it clones public repositories alone and cannot push."
    echo "Flows: guess_number and code_and_review, under Flows in the left panel."
else
    echo "No Claude Code agent: CLAUDE_CODE_OAUTH_TOKEN is not set."
    echo "Flows: guess_number, under Flows in the left panel."
fi
echo "Ctrl-C stops the chat server only; stop.sh also stops PostgreSQL and the tools."
echo

# 7. The server. A worker container left by a killed server would hold the name
#    the command gives it. exec keeps this pid, so stop.sh can find the server.
docker_ rm -f robinauts-extended-claude >/dev/null 2>&1 || true
echo $$ >"$state/server.pid"
exec "$venv/bin/robinauts" start --dev-no-sign-in --host 127.0.0.1 --port "$port"
