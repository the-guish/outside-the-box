# robinlocal

Robinauts from the zip of a branch, in Docker, answering with
[claude-everywhere](../claude-everywhere). It runs the local development mode: no sign-in,
one local user, loopback only.

```sh
./start.sh            # http://127.0.0.1:8010/
./refresh.sh          # download the zip again and restart
./stop.sh             # --reset also deletes the volumes
```

## How it runs

- `start.sh` starts claude-everywhere with its own `run.sh -d` when nothing answers on
  port 8787. It asks for the token password. Openworker reaches the same server.
- `start.sh` uses only a token that exists. Without one, it stops and says so. Create the
  token by running `../claude-everywhere/run.sh` once in a terminal of its own.
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

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `ROBINLOCAL_ZIP_URL` | the `dev` branch of `the-guish/robinauts` | Zip to download. |
| `ROBINLOCAL_PORT` | `8010` | Robinauts port. |
| `ROBINLOCAL_PG_PORT` | `54396` | PostgreSQL port, on loopback. |
| `CLAUDE_EVERYWHERE_PORT` | `8787` | claude-everywhere port. |
| `CLAUDE_EVERYWHERE_API_KEY` | unset | Key claude-everywhere asks for, when it has one. |
| `CLAUDE_EVERYWHERE_DIR` | `../claude-everywhere` | Where `run.sh` is. |
| `ROBINLOCAL_CLOUD` | `1` | `1` gives both agents claude-everywhere's cloud tools: `start_cloud_session` and `ask_about_cloud_session`. |

A changed setting takes effect at the next `./start.sh`.
