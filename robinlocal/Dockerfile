FROM node:24-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv

# The volumes mounted here start with this ownership.
RUN mkdir /work /venv /cache && chown node:node /work /venv /cache
USER node

ENV UV_CACHE_DIR=/cache/uv \
    UV_PYTHON_INSTALL_DIR=/cache/python \
    UV_PROJECT_ENVIRONMENT=/venv \
    npm_config_cache=/cache/npm
