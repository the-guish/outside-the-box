# syntax=docker/dockerfile:1
# Robinauts' Claude Code worker, as its own Dockerfile builds it, with the claude-cloud engine
# beside claude-code (README.md). The build context is this folder. build.sh names Robinauts
# at the commit of robinauts.pin as the build context "robinauts".

FROM python:3.12-slim

# What Claude Code's tools reach for in a workspace: git for the agent, ripgrep for Grep. The
# claude-cloud engine runs git too, on tincan.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git ripgrep \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_PREFERENCE=only-system \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/robinauts/venv

# Robinauts' worker, installed from its lock as Robinauts' own Dockerfile installs it.
WORKDIR /opt/robinauts/src
COPY --from=robinauts LICENSE NOTICE ./
COPY --from=robinauts backend/pyproject.toml backend/uv.lock backend/hatch_build.py backend/
COPY --from=robinauts backend/src backend/src
COPY --from=robinauts extra-workers/claude-code-worker/pyproject.toml \
    extra-workers/claude-code-worker/uv.lock extra-workers/claude-code-worker/
COPY --from=robinauts extra-workers/claude-code-worker/src extra-workers/claude-code-worker/src
COPY --from=robinauts extra-workers/worker-common/pyproject.toml extra-workers/worker-common/
COPY --from=robinauts extra-workers/worker-common/src extra-workers/worker-common/src
RUN cd extra-workers/claude-code-worker && uv sync --locked --no-dev \
    --no-install-package neorc --no-install-package neorc-core --no-install-package oneberth

# The engine, without dependencies: it moves no version of the worker's lock.
COPY pyproject.toml /opt/claude-cloud-worker/
COPY src /opt/claude-cloud-worker/src
RUN uv pip install --python /opt/robinauts/venv/bin/python --no-deps /opt/claude-cloud-worker

RUN useradd --system --create-home --home-dir /var/lib/robinauts --shell /usr/sbin/nologin \
        robinauts \
    && mkdir -p /var/lib/claude /workspace \
    && chown robinauts:robinauts /var/lib/claude /workspace
USER robinauts
# The engine keeps its records and its clone of tincan in $CLAUDE_CONFIG_DIR/cloud.
ENV CLAUDE_CONFIG_DIR=/var/lib/claude \
    ROBINAUTS_CLAUDE_WORKSPACES=/workspace \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    PATH=/opt/robinauts/venv/bin:$PATH
VOLUME ["/var/lib/claude", "/workspace"]

CMD ["python", "-m", "claude_cloud_worker"]
