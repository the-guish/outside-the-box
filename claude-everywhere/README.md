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

## Client tools

An Anthropic request can bring its own tools. Claude calls them, and the client runs them, as
with Anthropic's API. The OpenAI API does not pass tools yet.

- The server offers the request's tools to `claude` as an MCP server of its own, at
  `/mcp/<run>`. Each run has its own token.
- When Claude calls a tool, `claude` waits on the MCP call. The reply ends with the
  `tool_use` blocks and `stop_reason: "tool_use"`.
- The client runs the tools and sends the `tool_result` blocks in its next request. They
  answer the waiting calls, and the same `claude` run goes on in that request's reply.
- A run waits `CLAUDE_EVERYWHERE_TOOL_TIMEOUT` seconds for the results, then ends. Results
  that come later, or after a restart, start a new session. The calls and results then go
  into its transcript.
- A waiting run keeps its `claude` process and counts towards `MAX_CONCURRENCY`.

`CLAUDE_EVERYWHERE_SERVER_TOOLS` names Claude Code tools that run inside `claude` without
asking. The client never sees their calls. `WebSearch` searches through your Claude account,
for example. A server tool replaces the client tool of the same purpose: `WebSearch`
replaces `web_search` and `WebFetch` replaces `web_fetch`. Claude then has one search tool.
The client's approvals and logs do not cover server tools.

## Cloud sessions

`/cloud/mcp` is an MCP server with two tools. `start_cloud_session` starts a Claude Code
session in the cloud (claude.ai/code) on a GitHub repository, and returns the session's link.
An agent platform such as Robinauts can then hand work to a cloud session. The session goes
on working by itself. `ask_about_cloud_session` answers a question about a session, from its
log.

- `CLAUDE_EVERYWHERE_CLOUD_REPOS` lists the repositories the tool may use. Without it,
  `/cloud/mcp` answers 404.
- The tool takes `repo`, `prompt`, and optionally `branch` and `model`.
- Each call makes a shallow clone of the repository, then runs `claude --cloud` in it. The
  cloud session fetches the repository from GitHub itself.
- `claude --cloud` starts a session only on a terminal, so it runs in a pseudo-terminal. The
  clone is marked trusted in `.claude.json` for that run, then deleted with its trust.
- `--safe-mode` keeps the clone's hooks, settings and MCP servers from running.
- A private repository needs `CLAUDE_EVERYWHERE_GITHUB_TOKEN` for the clone.
- A cloud session needs a full sign-in. `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY`
  are not enough, so the server removes them for `claude --cloud`. In Docker, sign in once
  with `docker exec -it claude-everywhere claude /login`. The sign-in stays in the volume.
- Clients send `CLAUDE_EVERYWHERE_API_KEY` as with the other APIs.

`ask_about_cloud_session` takes `session` (an id or a link), `question`, and optionally
`model`, `haiku` by default.

- The question goes to `claude -p`, whose RemoteTrigger tool reads the session's log. It
  takes about 10 seconds.
- The tool reads any cloud session of the signed-in account.
- A log can quote anything its session read. A PreToolUse hook lets that run call only
  `get_run_log`, on the session asked about.
- `--safe-mode` drops that hook, so this run goes without it. CLAUDE.md files and memory are
  off through the environment.
- RemoteTrigger is off when `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` is set, so this run
  keeps it unset.

## What `claude` loads

Each run loads only what answering needs:

- `--system-prompt` replaces Claude Code's coding-agent prompt with "You are a helpful
  assistant."
- `--safe-mode` and `--strict-mcp-config` skip your configuration: CLAUDE.md files, hooks,
  plugins, skills and MCP servers. `claude` runs as you, so it would otherwise read your
  `~/.claude` on every request.
- A run with client tools cannot use `--safe-mode`, which also turns off the server's MCP
  bridge. It skips CLAUDE.md files and memory with `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and
  `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, and hooks with the `disableAllHooks` setting.
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
| `CLAUDE_EVERYWHERE_SERVER_TOOLS` | empty | Comma-separated Claude Code tools that run without asking, such as `WebSearch`. See [Client tools](#client-tools). |
| `CLAUDE_EVERYWHERE_WORKDIR` | `~/.local/share/claude-everywhere/workdir` | Directory `claude` runs in. Sessions belong to it. |
| `CLAUDE_EVERYWHERE_DB` | `~/.local/share/claude-everywhere/sessions.db` | Conversation-to-session map. |
| `CLAUDE_EVERYWHERE_EXTRA_ARGS` | empty | More `claude` arguments, shell-quoted. |
| `CLAUDE_EVERYWHERE_MAX_CONCURRENCY` | `4` | `claude` processes running at once. |
| `CLAUDE_EVERYWHERE_TIMEOUT` | `600` | Seconds a run may work before it is killed. Waiting for tool results does not count. |
| `CLAUDE_EVERYWHERE_TOOL_TIMEOUT` | `600` | Seconds a run waits for the client's tool results. |
| `CLAUDE_EVERYWHERE_CLAUDE_BIN` | `claude` | The CLI to run. |
| `CLAUDE_EVERYWHERE_CLOUD_REPOS` | empty | Comma-separated `owner/name` repositories for cloud sessions. See [Cloud sessions](#cloud-sessions). |
| `CLAUDE_EVERYWHERE_GITHUB_TOKEN` | unset | Token that clones private repositories for cloud sessions. |

Tools are off by default because the API can reach whatever the tools can. With tools on,
`claude -p` still denies the ones that need permission, unless `EXTRA_ARGS` sets a
permission mode.

## Limits

- Text only. Images are dropped, and so are tool calls and results sent to the OpenAI API.
- A tool result reaches Claude as text.
- `temperature`, `max_tokens` and similar parameters are ignored.
- A reply stops with `end_turn` or `tool_use` (Anthropic), or `stop` (OpenAI).
- An Anthropic request must end with a user message. A prefilled assistant reply is an error.
- `/v1/messages/count_tokens` and the other Anthropic endpoints do not exist.
- Each turn starts a `claude` process, which adds a second or two. A turn that calls client
  tools keeps its process until the results arrive.

## Tests

```sh
uv run pytest                                   # fake claude, a few seconds
RUN_REAL_CLAUDE=1 uv run pytest -m real_claude  # the real CLI, with haiku
```

The API tests start a real server and call it with the OpenAI and Anthropic SDKs. `tests/fake_claude.py`
stands in for the CLI. It keeps sessions on disk and calls client tools through the MCP
bridge.
