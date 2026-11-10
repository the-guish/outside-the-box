"""Offers a client's tools to `claude -p` as an MCP server, and holds a run while the client
runs the tools Claude called.

A run that calls client tools pauses: claude waits on its MCP calls while the reply goes to
the client with the calls. The client's next request carries the results. They answer the
waiting MCP calls, and the run goes on, now streaming into that request.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid
from collections.abc import AsyncIterator

from .core import MCP_SERVER, Tool, ToolResult

log = logging.getLogger(__name__)
NO_RESULT = ToolResult("The client sent no result for this call.", is_error=True)
CLOSED = ToolResult("The request that called this tool has ended.", is_error=True)


class Run:
    """A claude process with client tools, and its calls waiting for results."""

    def __init__(self, bridge: Bridge, tools: tuple[Tool, ...]):
        self.bridge = bridge
        self.id = uuid.uuid4().hex
        self.token = secrets.token_urlsafe(32)
        self.tools = tools
        self.events: AsyncIterator | None = None
        self.waiting: tuple[str, ...] = ()
        self._results: dict[str, asyncio.Future[ToolResult]] = {}
        self._expiry: asyncio.Task | None = None
        self._closed = False

    def mcp_config(self) -> str:
        server = {"type": "http", "url": f"{self.bridge.base_url}/mcp/{self.id}",
                  "headers": {"Authorization": f"Bearer {self.token}"}}
        return json.dumps({"mcpServers": {MCP_SERVER: server}})

    def result(self, call_id: str) -> asyncio.Future[ToolResult]:
        """The result of a call, awaited by its MCP request and set by the client's request."""
        if call_id not in self._results:
            self._results[call_id] = asyncio.get_running_loop().create_future()
        return self._results[call_id]

    def pause(self, call_ids: tuple[str, ...]) -> None:
        self.waiting = call_ids
        for call_id in call_ids:
            self.bridge.by_call[call_id] = self
        self._expiry = asyncio.create_task(self._expire())

    def resume(self, results: dict[str, ToolResult]) -> None:
        self._expiry.cancel()
        for call_id in self.waiting:
            self.bridge.by_call.pop(call_id, None)
            future = self.result(call_id)
            if not future.done():
                future.set_result(results.get(call_id, NO_RESULT))
        self.waiting = ()

    async def close(self) -> None:
        """Ends the run and its claude process. Calls still waiting get an error."""
        if self._closed:
            return
        self._closed = True
        if self._expiry and self._expiry is not asyncio.current_task():
            self._expiry.cancel()
        for call_id in self.waiting:
            self.bridge.by_call.pop(call_id, None)
        self.bridge.runs.pop(self.id, None)
        for future in self._results.values():
            if not future.done():
                future.set_result(CLOSED)
        if self.events:
            await self.events.aclose()

    async def _expire(self) -> None:
        await asyncio.sleep(self.bridge.tool_timeout)
        log.warning("no results for %s after %.0fs, ending the run", self.waiting, self.bridge.tool_timeout)
        await self.close()


class Bridge:
    def __init__(self, base_url: str, tool_timeout: float):
        self.base_url = base_url
        self.tool_timeout = tool_timeout
        self.runs: dict[str, Run] = {}
        self.by_call: dict[str, Run] = {}

    def new_run(self, tools: tuple[Tool, ...]) -> Run:
        run = Run(self, tools)
        self.runs[run.id] = run
        return run

    def claim(self, results: dict[str, ToolResult]) -> Run | None:
        """The paused run that made the calls these results answer, if it still waits."""
        runs = {self.by_call.get(call_id) for call_id in results}
        return runs.pop() if len(runs) == 1 and None not in runs else None

    async def close(self) -> None:
        for run in list(self.runs.values()):
            await run.close()

    async def handle(self, run: Run, message: dict) -> dict | None:
        """Answers one JSON-RPC message from claude's MCP client; None for a notification."""
        if "id" not in message:
            return None
        method = message.get("method")
        params = message.get("params") or {}
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion"),
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "claude-everywhere", "version": "1"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{"name": t.name, "description": t.description,
                                 "inputSchema": t.input_schema} for t in run.tools]}
        elif method == "tools/call":
            call_id = (params.get("_meta") or {}).get("claudecode/toolUseId")
            outcome = await run.result(call_id) if call_id else NO_RESULT
            result = {"content": [{"type": "text", "text": outcome.text}], "isError": outcome.is_error}
        else:
            return {"jsonrpc": "2.0", "id": message["id"],
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": message["id"], "result": result}
