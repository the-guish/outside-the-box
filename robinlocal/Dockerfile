FROM node:24-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl socat unzip \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv
# The server starts the Claude Code worker with docker run, and entry.sh builds its image.
COPY --from=docker:29-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:29-cli /usr/local/libexec/docker/cli-plugins/docker-buildx \
    /usr/local/libexec/docker/cli-plugins/docker-buildx

# The volumes mounted here start with this ownership.
RUN mkdir /work /venv /cache /config && chown node:node /work /venv /cache /config
USER node

ENV UV_CACHE_DIR=/cache/uv \
    UV_PYTHON_INSTALL_DIR=/cache/python \
    UV_PROJECT_ENVIRONMENT=/venv \
    npm_config_cache=/cache/npm
