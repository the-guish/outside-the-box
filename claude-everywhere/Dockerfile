FROM python:3.12-slim

ARG CLAUDE_VERSION=2.1.314

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv

RUN useradd --create-home --uid 1000 app && mkdir /data && chown app:app /data
USER app
WORKDIR /home/app

# A pipe would hide a failed download, so the build checks that claude runs.
RUN curl -fsSL -o /tmp/install.sh https://claude.ai/install.sh \
    && bash /tmp/install.sh "$CLAUDE_VERSION" && rm /tmp/install.sh \
    && /home/app/.local/bin/claude --version

COPY --chown=app:app pyproject.toml uv.lock README.md app/
COPY --chown=app:app src app/src
RUN cd app && uv sync --frozen --no-dev

# /data keeps Claude's sessions and the conversation map across container restarts.
ENV PATH=/home/app/app/.venv/bin:/home/app/.local/bin:$PATH \
    CLAUDE_CONFIG_DIR=/data/claude \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_EVERYWHERE_HOST=0.0.0.0 \
    CLAUDE_EVERYWHERE_DB=/data/sessions.db \
    CLAUDE_EVERYWHERE_WORKDIR=/data/workdir
VOLUME /data
EXPOSE 8787
CMD ["claude-everywhere"]
