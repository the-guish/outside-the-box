# The repo's to-do list MCP server (mcp/to-do-list), installed from its lock.
# compose.yaml names that folder as the build context "source". serve.py says
# why this image does not run the package's own command.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_PREFERENCE=only-system \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/todo/venv

WORKDIR /opt/todo/src
COPY --from=source pyproject.toml uv.lock ./
COPY --from=source src src
RUN uv sync --locked --no-dev --no-editable

COPY serve.py /opt/todo/serve.py

RUN useradd --create-home --uid 10001 todo \
    && mkdir /data \
    && chown todo:todo /data

USER todo
EXPOSE 8944
CMD ["/opt/todo/venv/bin/python", "/opt/todo/serve.py"]
