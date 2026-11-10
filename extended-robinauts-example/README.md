# Extended Robinauts example

This example runs Robinauts with five MCP tool servers, the orchestrator's flows and,
optionally, the Claude Code worker. It extends the repo's `demo/`, which has two plain
assistants and no tools. Everything listens on this machine's loopback alone.

    ./start.sh [path-to-robinauts-repo]   # default ~/code/robinfork/robinauts
    ./stop.sh                             # the conversations stay
    ./stop.sh --reset                     # and delete them, and the to-do list

Then open <http://127.0.0.1:8000/>.

## What it needs

- Docker with compose, uv, and Node.js at the version in the repo's `frontend/.nvmrc`.
- A Robinauts checkout with `mcp/` and `orchestration/`.
- `OPENROUTER_API_KEY`, exported or in `.env` beside `start.sh`. `start.sh` reads `.env` only
  when it is mode 0600.
- For the Claude Code agent, optional: `CLAUDE_CODE_OAUTH_TOKEN`, from `claude setup-token`,
  and `WORKER_GITHUB_TOKEN`. Both are read like the key.

OpenRouter is the only paid service. The Claude Code agent runs on your Claude subscription.

## What you get

| Agent | Model | Tools |
|---|---|---|
| Researcher | Claude Sonnet 5 | all five, and `sessions` |
| Pi Sidekick | Claude Opus 5.5 | all five, and `sessions`, with turns of up to 30 minutes |
| Echo | none | none; a prompt starting with `poison` fails the turn |
| Claude Code | Claude Opus 5.5, on your subscription | Claude Code's own, with Bash, and `sessions` |

The model picker offers five OpenRouter models: Claude Opus 5.5, Claude Sonnet 5, Claude
Haiku 4.5, GPT-5.5 and Gemini 3.8 Flash.

## The MCP tool servers

Each server runs in a container of its own, on a Docker network of its own.

| Server | Port | What it is |
|---|---|---|
| fetch | 8941 | `mcp-server-fetch` behind `mcp-proxy`: reads a page as Markdown |
| browser | 8942 | Playwright MCP, headless Chromium |
| files | 8943 | the reference filesystem server on `~/agents-fs`, behind a bearer key |
| todo | 8944 | the repo's `mcp/to-do-list`, shown in the side panel |
| diagrams | 8945 | the repo's `mcp/diagrams`, shown animated in the side panel |

The simplest example is fetch: a third-party server, three lines in `fetch/Dockerfile` and
four in `robinauts.toml`. The todo and diagrams servers show a server with a view in the
interface.

The files server reads and writes one host folder, `~/agents-fs`. `EXTENDED_FILES_DIR` names
another. `start.sh` makes its bearer key once, in `.state/files-key`.

The todo and diagrams servers have no credential. They answer only a loopback `Host`, which
their `serve.py` checks before serving.

## The Claude Code worker

The Claude Code agent is on when `CLAUDE_CODE_OAUTH_TOKEN` is set. `start.sh` then builds the
repo's `extra-workers/claude-code-worker` image, and the server runs it with `docker run`. Each
conversation gets `/workspace/<conversation id>` in the container. Its transcripts and
workspaces live in two Docker volumes.

The agent has Bash, so its container stays on Docker's bridge network. The server listens on
127.0.0.1, and a `socat` relay (`relay` in `compose.yaml`) forwards the bridge's address to it.
The relay takes connections from the bridge's subnet alone. The agent reaches the server and
the internet. The tool servers and the databases are closed to it.

The worker's tables are in `claude-worker.toml.in`. `start.sh` appends them to `robinauts.toml`
in `.state/robinauts.toml`, with the paths and the broker URL filled in.

`WORKER_GITHUB_TOKEN` is optional. It is a fine-grained GitHub token, passed to the container as
`GITHUB_TOKEN`, for the repositories the agent works on. Without it the agent clones public
repositories alone and cannot push. The agent commits as `git config user.name` and
`user.email`.

## Flows

`start.sh` copies the repo's flows into `.state/flows` and points the server at them with
`ROBINAUTS_FLOWS_DIR`. The server then runs neorc at `/neorc`, and the interface shows **Flows**
in its left panel.

- `guess_number`: a game on the Haiku model judges a guesser's tries at a number from 1 to 10.
- `code_and_review`, with the Claude Code worker alone: an agent does a task on a repository and
  opens a pull request, and a second conversation reviews it. Its form offers one repository,
  `EXTENDED_REPO_URL` (default `https://github.com/the-guish/robinauts`) at
  `EXTENDED_REPO_BRANCH` (default `main`). Each Claude Code session also gets a worktree of it
  on `rb/<session id>`.

## Ports and state

PostgreSQL listens on 54392 and the server on 8000 (`ROBINAUTS_DEMO_PORT`). These differ from
the repo demo's database port, so both can run, on different server ports.

`.state/` holds the Python environment, the configuration, the database password, the files
key, the flows folder and the server's pid. The conversations, the to-do list and the Claude
Code worker's files live in Docker volumes. `./stop.sh --reset` deletes them all.
