# The repo's diagrams MCP server (mcp/diagrams), installed from its lock.
# compose.yaml names that folder as the build context "source". serve.py says
# why this image does not run the package's own command.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_PREFERENCE=only-system \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/diagrams/venv

WORKDIR /opt/diagrams/src
COPY --from=source pyproject.toml uv.lock ./
COPY --from=source src src
RUN uv sync --locked --no-dev --no-editable

COPY serve.py /opt/diagrams/serve.py

RUN useradd --create-home --uid 10001 diagrams

USER diagrams
EXPOSE 8945
CMD ["/opt/diagrams/venv/bin/python", "/opt/diagrams/serve.py"]
