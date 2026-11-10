# The reference MCP filesystem server behind supergateway, which serves it over
# Streamable HTTP. Node only. The server sees /agents-fs alone: the one folder
# compose.yaml mounts, $HOME/agents-fs on the host.
FROM node:24-slim

RUN npm install --global --no-fund --no-audit \
    supergateway@4.2.0-rc.1 @modelcontextprotocol/server-filesystem@2026.8.31

USER node
EXPOSE 8943
# The key comes from SUPERGATEWAY_API_KEY: every request must carry it as a
# bearer, which a web page rebinding a name to 127.0.0.1 cannot know. It is
# the only guard: supergateway checks no Host. 4.2.0-rc.1, a release candidate,
# because 4.1.0, the latest release, has no API key at all.
CMD ["supergateway", "--stdio", "mcp-server-filesystem /agents-fs", \
     "--outputTransport", "streamableHttp", "--host", "0.0.0.0", "--port", "8943", \
     "--stateful"]
