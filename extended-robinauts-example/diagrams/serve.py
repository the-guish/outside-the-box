# Starts the repo's diagrams server so that it listens for
# Docker's published port and keeps its loopback-only Host and Origin checks.
#
# The MCP SDK turns those checks on when the server is built with a loopback
# host, and reads the address to bind later. So the server is built for
# 127.0.0.1 and then bound to 0.0.0.0. The port is published on the host's
# loopback alone (compose.yaml).

from diagrams.server import build_server

server = build_server("127.0.0.1", 8945)
security = server.settings.transport_security
if security is None or not security.enable_dns_rebinding_protection:
    raise SystemExit("the MCP SDK did not turn on its Host check: refusing to serve")
server.settings.host = "0.0.0.0"
server.run("streamable-http")
