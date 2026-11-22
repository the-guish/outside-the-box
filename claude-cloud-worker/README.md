# claude-cloud-worker

claude-cloud-worker is Robinauts' Claude Code worker with one more engine, `claude-cloud`. A
conversation on that engine is a cloud session of Claude Code, on claude.ai/code.

- A person chats with the cloud session in Robinauts, as with any agent.
- A neorc flow gives the session a task the same way: its step names an agent on `claude-cloud`.
- The worker still runs the agents on `claude-code`.

The code comes from the branch `claude/jolly-gates-hkm1yv` of `the-guish/fa1`. The start of a
session and the sign-in changed here, after a run against the real claude.ai ("Verified" below).

## The parts

| part | file | where it runs |
|---|---|---|
| the engine | `src/claude_cloud_worker/engine.py` | the worker |
| `claude --cloud`, in a terminal | `src/claude_cloud_worker/cloud.py` | the worker |
| tincan's reader and the poller | `src/claude_cloud_worker/tincan.py` | the worker |
| the worker's front | `src/claude_cloud_worker/__main__.py` | the worker |
| the hooks | `environment/setup.sh`, which writes `tincan_hook.py` | the cloud session |
| the image | `Dockerfile`, `build.sh`, `robinauts.pin` | Docker |

- Robinauts' worker refuses an agent on an engine other than `claude-code`. The front widens that
  check to `claude-cloud`, then runs Robinauts' worker as it is.
- The image installs the engine without dependencies. Every version of the worker's lock stays.
- The engine registers under the entry point `robinauts.engines`. Robinauts finds it there.

## A turn

1. The engine writes the prompt. The first line is the marker `[tincan <conversation id> turn
   <n>]`. The first turn adds the agent's system prompt, in `<instructions>`.
2. A first turn starts a session: `claude --model <the model's name> --cloud=<prompt>`, in a
   pseudo-terminal, from the clone of tincan. The engine reads the session's link from the screen.
3. A later turn sends a message: `claude -p <prompt> --cloud <session id> --output-format json`.
   It returns before the reply.
4. The engine gives the session's link as the turn's first line of reasoning.
5. The cloud session's hooks write its progress and its reply to tincan ("Tincan" below).
6. The engine polls tincan, and gives the reply as the answer, with a new checkpoint.

## The sign-in

A cloud session needs a full sign-in of claude.ai, the one `claude /login` writes. The token of
`claude setup-token` is refused: "Unable to get organization UUID".

- The sign-in lives in the worker's volume, at `$CLAUDE_CONFIG_DIR/.credentials.json`.
- `python -m claude_cloud_worker.sign_in <source> <target>` keeps that file equal to the
  sign-in of a claude that runs elsewhere, without its refresh token. That claude alone
  refreshes the sign-in. The process looks every 30 seconds.
- `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY` win over that file. The engine runs
  `claude --cloud` without them. The `claude-code` engine keeps them.
- `claude --cloud` asks to trust a new folder, and shows first-run screens in a new
  configuration. The engine answers both in `$CLAUDE_CONFIG_DIR/.claude.json` before a start.

## Tincan

Tincan is a GitHub repository of the person's own, with a commit on its default branch. Each
conversation writes one branch of it, `s/<conversation id>`:

| file | written | holds |
|---|---|---|
| `session.json` | at the first prompt | the conversation, the cloud session's id and link |
| `status.json` | at each prompt, each heartbeat and each stop | `working` or `idle`, the turn, the time of the last tool call |
| `turns/NNNN.md` | at the stop of turn NNNN | the reply |

- One cloud session writes each branch.
- The engine reads tincan alone. It clones it to `$CLAUDE_CONFIG_DIR/cloud/tincan`, and starts
  `claude --cloud` from that clone, so the session's repository is tincan.
- A forgotten conversation keeps its branch. The person deletes the branch.

The hooks, in the cloud session:

- `UserPromptSubmit` reads the marker. It writes `session.json` and `status.json`, and starts the
  heartbeat.
- `PostToolUse` touches a local file. It runs no git, so a tool call stays fast.
- The heartbeat writes `status.json` every 5 minutes while the turn runs.
- `Stop` writes `turns/<n>.md` from `last_assistant_message`, and `idle`.
- The hooks act on a marked prompt alone. A prompt typed on claude.ai gets no turn file.

## Polling

- One poller serves every waiting turn. Each cycle runs one `git ls-remote` on tincan.
- The engine fetches a branch only when its commit changed.
- The interval starts at 20 seconds. It grows by half at each cycle with no change, up to 2
  minutes. A change, or a new prompt, sets it back.

## When a turn fails

| case | after | the error says |
|---|---|---|
| `claude --cloud` starts no session, or sends no message | at once | what claude printed |
| the cloud session takes no prompt | 10 minutes | check the environment's tincan hooks |
| the branch gets no commit while the turn runs | 15 minutes, three missed heartbeats | the session has been silent |
| the agent's `turn_timeout_seconds` passes | the timeout | Robinauts' timeout |

- A cloud session goes on from its latest reply. A turn from an earlier checkpoint, such as an
  edit or a regenerate, is refused.
- A fork is refused. The person starts a new conversation.
- A resumed turn that finished gives its reply again. A resumed turn still running is waited for.
- A cancelled turn stops the wait. The cloud session goes on, and claude.ai shows it.
- Robinauts' chat shows one fixed sentence for a failed turn. The error is in `turns.error`.

## Setting it up

### The cloud environment, on claude.ai

1. Add tincan to the environment's repositories, with the work repositories beside it. The
   GitHub connection of claude.ai needs push access to tincan.
2. Add the variable `TINCAN_URL`, such as `https://github.com/<owner>/tincan.git`.
3. Paste `environment/setup.sh` into the environment's setup script.
4. Make it the default environment. `claude --cloud` starts its sessions there.

The setup script writes the hook to `/opt/tincan/tincan_hook.py`, and adds four hooks to
`~/.claude/settings.json` in the container.

### The worker

```sh
./build.sh                                   # the image claude-cloud-worker
docker run --rm -it -v <the worker's volume>:/var/lib/claude --entrypoint sh claude-cloud-worker -c \
  '"$(python -c "from claude_cloud_worker.settings import bundled_cli; print(bundled_cli())")" /login'
```

- The image takes the place of Robinauts' `claude-code-worker` image, with the same variables
  and volumes.
- The second command signs in once. The sign-in stays in the volume.
- The worker's GitHub token needs read access to tincan.

| variable | default | sets |
|---|---|---|
| `CLAUDE_CLOUD_TINCAN_URL` | none: every turn is refused | the tincan repository |
| `CLAUDE_CLOUD_ENVIRONMENT` | claude's default environment | the cloud environment of the sessions, by name or by id |
| `CLAUDE_CLOUD_POLL_SECONDS` | 20 | the shortest interval of the poller |
| `CLAUDE_CLOUD_POLL_MOST_SECONDS` | 120 | the longest interval |
| `CLAUDE_CLOUD_START_SECONDS` | 600 | the wait for the session to take a prompt |
| `CLAUDE_CLOUD_SILENT_SECONDS` | 900 | the wait for a commit while a turn runs |
| `CLAUDE_CLOUD_STATE` | `$CLAUDE_CONFIG_DIR/cloud` | the records and the clone of tincan |
| `ROBINAUTS_CLAUDE_CLI_PATH` | the binary the Agent SDK bundles | the `claude` the engine runs |

### The agent, in Robinauts' configuration

```toml
[agents.cloud]
title = "Claude in the cloud"
engine = "claude-cloud"
models = ["cc-haiku"]
worker = "claude"
turn_timeout_seconds = 86400
system_prompt = "Say what you did."
```

The cloud session runs on the model of the conversation: the engine passes the model's `name` to
`--model`.

## Tests

`tests/run.sh` runs the engine and the hook in the environment of Robinauts' worker at the commit
of `robinauts.pin`, on real git, outside Docker. `tests/fake_claude.py` stands in for `claude`:
it starts a session on a terminal alone, refuses a run that holds a token, and runs the real hook
against a bare repository.

```sh
tests/run.sh
```

## Verified

Against the real claude.ai on 2026-11-21, by hand, with Claude Code 2.1.319 in the worker's image
and 2.1.330 on the host:

- `claude -p --cloud <prompt>` is refused: a new session is interactive only.
- `claude --cloud=<prompt>` in a pseudo-terminal starts a session and prints its link.
- `--model haiku` sets the session's model.
- `claude -p <message> --cloud <session id> --output-format json` reaches the session, and
  answers `{"ok": true, "session_id": …, "url": …}` at once.
- The token of `claude setup-token` is refused, and so is a run that holds it beside a sign-in.
- A cloud session pushes a branch of any name to a repository of its environment.

## Open

These need a cloud environment with the hooks, which no run had yet:

- The hooks of `~/.claude/settings.json` in a cloud container. The home there is `/root`.
- `CLAUDE_CODE_REMOTE_SESSION_ID` in a hook's environment.
- The heartbeat, a process the hook starts in a session of its own, alive after the hook ends.
- The hook's push to tincan from a session that holds several repositories.
- A sign-in that lasts: `claude /login` in the worker's volume, and its renewal there.
