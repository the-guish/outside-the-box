# robinlocal

Robinauts from the zip of a branch, in Docker, answering with
[claude-everywhere](../claude-everywhere). It runs the local development mode: no sign-in,
one local user, loopback only. It also has a local MCP server, Robinauts' two MCP apps, and
Claude Code in a container.

```sh
./start.sh            # http://127.0.0.1:8010/
./refresh.sh          # download the zip again and restart
./stop.sh             # --reset also deletes the volumes
```

## How it runs

- `start.sh` starts claude-everywhere with its own `run.sh -d` when nothing answers on
  port 8787. Openworker reaches the same server.
- claude-everywhere needs a sign-in, made once. `start.sh` stops without one and prints the
  command that signs in.
- The `work` volume holds the unzipped source, its `node_modules` and the built interface.
  The `venv` and `cache` volumes hold the installed backend and the caches of uv and npm.
- Both agents start Claude Code cloud sessions. `start.sh` starts claude-everywhere with
  `the-guish/robinauts` as the allowed repository, or with `CLAUDE_EVERYWHERE_CLOUD_REPOS`.
  The agents run without the tool if claude-everywhere has no cloud endpoint.
- `refresh.sh` replaces the source in the `work` volume and restarts the container. The
  image is not rebuilt. `node_modules` stays when `package-lock.json` did not change.
- `container/` is mounted into the container. A change to `entry.sh` needs only a restart.
- Robinauts uses the host's network, because its development mode binds loopback only. It
  reaches PostgreSQL and claude-everywhere on `127.0.0.1`. This works on Linux only.

The models are `sonnet`, `opus` and `haiku`, passed to `claude --model`. The two agents
run on LangGraph and on Pydantic AI. `container/entry.sh` writes the configuration.

## Tools

Both agents have these tools:

- `fetch` reads a web page as text. It is a local MCP server in a container of its own.
- `todo` and `diagrams` are Robinauts' MCP apps, and their views open in the side panel.
  They run beside the server, from the downloaded source. The to-do list is kept in the
  `work` volume, and the diagram in memory.

## Claude Code in a container

The Claude Code agent appears when `ROBINLOCAL_CLAUDE_CODE_TOKEN` is set. Make the token
with `claude setup-token`, then put it in `.env` beside this file, which git ignores:

```sh
ROBINLOCAL_CLAUDE_CODE_TOKEN=<what claude setup-token printed>
```

- The agent runs on your subscription, with Bash in a directory for each conversation.
- Its container stays off the host's network. It reaches the server through a relay on
  Docker's bridge address, and it cannot reach the host's loopback.
- The server starts this container, so it gets the Docker socket. That socket is root on
  this machine. Without the token, the server does not get it.
- The sign-in of claude-everywhere is separate from this token.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `ROBINLOCAL_ZIP_URL` | the `main` branch of `the-guish/robinauts` | Zip to download. |
| `ROBINLOCAL_PORT` | `8010` | Robinauts port. |
| `ROBINLOCAL_PG_PORT` | `54396` | PostgreSQL port, on loopback. |
| `ROBINLOCAL_FETCH_PORT` | `8942` | `fetch` port, on loopback. |
| `ROBINLOCAL_TODO_PORT` | `8946` | `todo` port, on loopback. |
| `ROBINLOCAL_DIAGRAMS_PORT` | `8947` | `diagrams` port, on loopback. |
| `ROBINLOCAL_CLAUDE_CODE_TOKEN` | unset | Token of the Claude Code agent. |
| `CLAUDE_EVERYWHERE_PORT` | `8787` | claude-everywhere port. |
| `CLAUDE_EVERYWHERE_API_KEY` | unset | Key claude-everywhere asks for, when it has one. |
| `CLAUDE_EVERYWHERE_DIR` | `../claude-everywhere` | Where `run.sh` is. |
| `ROBINLOCAL_CLOUD` | `1` | `1` gives both agents claude-everywhere's cloud tools: `start_cloud_session` and `ask_about_cloud_session`. |

A changed setting takes effect at the next `./start.sh`. The database must match the
source. If `robinauts db init` refuses it after a new download, `./stop.sh --reset` makes it
again and deletes the conversations.
