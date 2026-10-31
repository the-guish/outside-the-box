# claude-everywhere

An OpenAI Chat Completions API and an Anthropic Messages API that answer with `claude -p`.
Tools that speak one of these APIs can then use Claude Code, with your Claude login.

## Run

```sh
uv sync
uv run claude-everywhere          # http://127.0.0.1:8787/v1
```

Point an OpenAI client at `http://127.0.0.1:8787/v1`. Any API key works unless
`CLAUDE_EVERYWHERE_API_KEY` is set.

```sh
curl http://127.0.0.1:8787/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model": "sonnet", "messages": [{"role": "user", "content": "hello"}]}'
```

Point an Anthropic client at `http://127.0.0.1:8787`, for example with
`ANTHROPIC_BASE_URL`. It sends the key as `x-api-key`.

```sh
curl http://127.0.0.1:8787/v1/messages -H 'Content-Type: application/json' \
  -d '{"model": "sonnet", "max_tokens": 1024, "messages": [{"role": "user", "content": "hello"}]}'
```

## Run in Docker

```sh
./run.sh        # add -d to run in the background
```

`run.sh` builds the image and serves on `http://127.0.0.1:8787/v1`. The container needs a
credential of its own:

- `CLAUDE_CODE_OAUTH_TOKEN`: a long-lived token for your subscription. `run.sh` uses it
  when it is set. Otherwise `run.sh` keeps one in
  `~/.config/claude-everywhere/oauth-token.gpg`, encrypted with a password.
- `ANTHROPIC_API_KEY`: an API key, billed per use. `run.sh` passes it when it is set.

The first run creates the encrypted token. `scripts/token_vault.py` runs
`claude setup-token`, which signs you in through the browser. The script hides the token
from the screen and asks for a new password. Each later run asks for that password.
Delete the file to create a new token.

The container does not use `~/.claude/.credentials.json`. That login renews itself, and a
container renewing it could sign out the `claude` on your machine.

The `claude-everywhere-data` volume keeps Claude's sessions and the conversation map.
`run.sh` passes the `CLAUDE_EVERYWHERE_*` settings below, except the host, directories and
binary. `CLAUDE_VERSION` picks the Claude Code version at build time.

Inside Docker, tools reach only the container. `CLAUDE_EVERYWHERE_TOOLS=default` is
therefore much safer there than on your machine.

## How conversations map to sessions

Clients of both APIs send the whole conversation on every request. The server keeps one Claude
session per conversation and sends `claude` only the new message.

- Each reply is stored in SQLite under a hash of the conversation up to and including it.
- A request looks up the hash of every message except the last one. A match runs
  `claude -p --resume <session> --fork-session` with the last message.
- Forking gives each reply its own session. Regenerating a reply or editing an earlier
  message therefore branches from the right point.
- A conversation with no match starts a new session. Its earlier messages go into the
  prompt as a transcript. This happens with histories from elsewhere, or when the client
  changed an earlier reply.
- System messages become `--append-system-prompt` when a session starts. Resumed sessions
  keep them. An Anthropic `system` prompt counts as the first system message.
- Both APIs share the map. A conversation started with one API can go on with the other.

## What `claude` loads

Each run loads only what answering needs:

- `--system-prompt` replaces Claude Code's coding-agent prompt with "You are a helpful
  assistant."
- `--safe-mode` and `--strict-mcp-config` skip your configuration: CLAUDE.md files, hooks,
  plugins, skills and MCP servers. `claude` runs as you, so it would otherwise read your
  `~/.claude` on every request.
- `--disable-slash-commands` stops a message like `/usage` from running as a command. The
  server also adds a space before a leading `/`, so such a message reaches the model as text.
- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` turns off telemetry and error reports.

Your settings for the model and permissions still apply.

## Models

`/v1/models` lists `claude-code`, `fable`, `haiku`, `opus` and `sonnet`. A request with an
`anthropic-version` header gets Anthropic's format. The aliases and
full names such as `claude-sonnet-5-5` are passed to `--model`. Any other name, such as
`gpt-4o`, uses `CLAUDE_EVERYWHERE_MODEL`, or Claude's own default when that is unset.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_EVERYWHERE_HOST` | `127.0.0.1` | Bind address. |
| `CLAUDE_EVERYWHERE_PORT` | `8787` | Port. |
| `CLAUDE_EVERYWHERE_API_KEY` | unset | Key clients must send, as a bearer token or `x-api-key`. |
| `CLAUDE_EVERYWHERE_MODEL` | unset | Model for names Claude does not know. |
| `CLAUDE_EVERYWHERE_TOOLS` | empty | Comma-separated `--tools`. Empty disables every tool; `default` gives the usual set. |
| `CLAUDE_EVERYWHERE_WORKDIR` | `~/.local/share/claude-everywhere/workdir` | Directory `claude` runs in. Sessions belong to it. |
| `CLAUDE_EVERYWHERE_DB` | `~/.local/share/claude-everywhere/sessions.db` | Conversation-to-session map. |
| `CLAUDE_EVERYWHERE_EXTRA_ARGS` | empty | More `claude` arguments, shell-quoted. |
| `CLAUDE_EVERYWHERE_MAX_CONCURRENCY` | `4` | `claude` processes running at once. |
| `CLAUDE_EVERYWHERE_TIMEOUT` | `600` | Seconds before a run is killed. |
| `CLAUDE_EVERYWHERE_CLAUDE_BIN` | `claude` | The CLI to run. |

Tools are off by default because the API can reach whatever the tools can. With tools on,
`claude -p` still denies the ones that need permission, unless `EXTRA_ARGS` sets a
permission mode.

## Limits

- Text only. Images, tool calls and tool results are dropped.
- `temperature`, `max_tokens` and similar parameters are ignored.
- A reply always stops with `end_turn` (Anthropic) or `stop` (OpenAI).
- An Anthropic request must end with a user message. A prefilled assistant reply is an error.
- `/v1/messages/count_tokens` and the other Anthropic endpoints do not exist.
- Each turn starts a `claude` process, which adds a second or two.

## Tests

```sh
uv run pytest                                   # fake claude, a few seconds
RUN_REAL_CLAUDE=1 uv run pytest -m real_claude  # the real CLI, with haiku
```

The API tests start a real server and call it with the OpenAI and Anthropic SDKs. `tests/fake_claude.py`
stands in for the CLI and keeps sessions on disk.
