# mcp-server-fetch behind mcp-proxy, so it is served over Streamable HTTP.
# The MCP SDK stays on 1.x: mcp 2.x breaks mcp-proxy 0.12.
FROM python:3.12-slim

RUN pip install --no-cache-dir "mcp<2" "mcp-proxy==0.12.0" mcp-server-fetch \
    && useradd --create-home --uid 10001 fetch

USER fetch
EXPOSE 8941
CMD ["mcp-proxy", "--host", "0.0.0.0", "--port", "8941", "--", "mcp-server-fetch"]
