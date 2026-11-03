#!/usr/bin/env bash
# Runs inside the robinauts container. `fetch` replaces the source with a fresh download
# of the zip; `serve` fetches only when there is no source yet, then builds and starts.
set -euo pipefail

SRC=/work/robinauts
CONFIG=/work/robinauts.toml

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
  echo "Source ready in the work volume."
}

write_config() {
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

[agents.assistant-pydantic-ai]
title = "Assistant (Pydantic AI)"
model = "sonnet"
engine = "pydantic-ai"
system_prompt = "You are a helpful assistant."
TOML
}

serve() {
  [[ -d $SRC ]] || fetch
  if [[ ! -f $SRC/frontend/dist/index.html ]]; then
    cd "$SRC/frontend"
    [[ -d node_modules ]] || npm ci --ignore-scripts
    npm run build
  fi
  cd "$SRC/backend"
  uv sync --locked --no-dev --python 3.12 --no-editable --reinstall-package robinauts --quiet
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
