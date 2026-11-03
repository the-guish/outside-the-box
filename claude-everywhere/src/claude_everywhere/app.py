"""OpenAI Chat Completions and Anthropic Messages APIs that answer with `claude -p`,
one Claude session per conversation."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .bridge import Bridge, Run
from .cloud import CloudServer
from .core import (
    DEFAULT_MODEL_NAME, MODEL_ALIASES, Message, Turn, anthropic_messages, anthropic_tools,
    bridged_name, normalize, reply_key, resolve_model, split_turn, tool_results, transcript_prompt,
)
from .runner import AwaitingTools, ClaudeError, RunResult, ToolUse, run_claude
from .sessions import SessionStore
from .settings import Settings

log = logging.getLogger("claude_everywhere")
MODELS = [DEFAULT_MODEL_NAME, *sorted(MODEL_ALIASES)]
ANTHROPIC_PATH = "/v1/messages"
ANTHROPIC_ERROR_TYPES = {400: "invalid_request_error", 401: "authentication_error",
                         404: "not_found_error"}
ANTHROPIC_USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens",
                        "cache_read_input_tokens", "output_tokens")


def openai_error(status: int, message: str, kind: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def anthropic_error_body(status: int, message: str) -> dict:
    kind = ANTHROPIC_ERROR_TYPES.get(status, "api_error")
    return {"type": "error", "error": {"type": kind, "message": message}}


def anthropic_error(status: int, message: str) -> JSONResponse:
    return JSONResponse(anthropic_error_body(status, message), status_code=status)


def openai_usage(usage: dict) -> dict:
    prompt = sum(usage.get(k, 0) for k in
                 ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    completion = usage.get("output_tokens", 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion}


def anthropic_usage(usage: dict) -> dict:
    return {k: usage.get(k, 0) for k in ANTHROPIC_USAGE_KEYS}


async def collect(events: AsyncIterator[str | RunResult]) -> tuple[str, RunResult]:
    text = ""
    result = None
    async for event in events:
        if isinstance(event, str):
            text += event
        else:
            result = event
    return text, result


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = SessionStore(settings.db_path)
    slots = asyncio.Semaphore(settings.max_concurrency)
    # claude reaches the bridge on this server. A wildcard bind address also listens on loopback.
    host = "127.0.0.1" if settings.host in ("0.0.0.0", "::", "") else settings.host
    bridge = Bridge(f"http://{f'[{host}]' if ':' in host else host}:{settings.port}",
                    settings.tool_timeout)
    cloud = CloudServer(settings, slots) if settings.cloud_repos else None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await bridge.close()
        store.close()

    app = FastAPI(title="claude-everywhere", lifespan=lifespan)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith(ANTHROPIC_PATH):
            return anthropic_error(exc.status_code, str(exc.detail))
        return openai_error(exc.status_code, str(exc.detail), "invalid_request_error")

    def authorize(request: Request) -> None:
        """OpenAI clients send the key as a bearer token, Anthropic clients as x-api-key."""
        if settings.api_key and settings.api_key not in (
            request.headers.get("x-api-key"),
            request.headers.get("authorization", "").removeprefix("Bearer "),
        ):
            raise HTTPException(401, "invalid API key")

    async def answer(turn: Turn, model: str | None, run: Run | None = None) -> AsyncIterator:
        """Resumes the conversation's session when known, else starts one from a transcript."""
        mcp_config = run.mcp_config() if run else None
        async with slots:
            session = store.get(turn.parent_key) if turn.history else None
            if session:
                started = False
                try:
                    async for event in run_claude(settings, turn.prompt, resume=session, model=model,
                                                  mcp_config=mcp_config):
                        started = True
                        yield event
                    return
                except ClaudeError as exc:
                    if started:
                        raise
                    log.warning("resuming session %s failed, starting a new one: %s", session, exc)
            prompt = transcript_prompt(turn.history, turn.prompt)
            async for event in run_claude(settings, prompt, system=turn.system or None, model=model,
                                          mcp_config=mcp_config):
                yield event

    async def reply(messages: list[Message], events: AsyncIterator, run: Run | None) -> AsyncIterator:
        """One reply's events: up to the result, or up to a pause for client tools. The first
        is read before this returns, so a failed start raises ClaudeError before any response.
        A result stores the reply's session. A paused run waits in the bridge; others close."""
        async def close() -> None:
            await (run.close() if run else events.aclose())

        try:
            first = await anext(events)
        except BaseException:
            await close()
            raise

        async def rest() -> AsyncIterator:
            paused = False
            try:
                event = first
                while event is not None:
                    if isinstance(event, RunResult) and event.session_id:
                        store.put(reply_key(messages, event.text), event.session_id)
                    if isinstance(event, AwaitingTools):
                        run.pause(event.ids)
                        paused = True
                    yield event
                    if paused:
                        return
                    event = await anext(events, None)
            finally:
                if not paused:
                    await close()

        return rest()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/models", dependencies=[Depends(authorize)])
    async def models(request: Request):
        if request.headers.get("anthropic-version"):
            return {"data": [
                {"type": "model", "id": m, "display_name": m, "created_at": "1970-01-01T00:00:00Z"}
                for m in MODELS
            ], "has_more": False, "first_id": MODELS[0], "last_id": MODELS[-1]}
        return {"object": "list", "data": [
            {"id": m, "object": "model", "created": 0, "owned_by": "anthropic"} for m in MODELS
        ]}

    @app.post("/v1/chat/completions", dependencies=[Depends(authorize)])
    async def chat_completions(request: Request):
        try:
            body = await request.json()
            messages = normalize(body["messages"])
            turn = split_turn(messages)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, f"invalid request: {exc}") from None

        requested_model = body.get("model") or DEFAULT_MODEL_NAME
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        model = resolve_model(requested_model, settings.default_model)
        try:
            events = await reply(messages, answer(turn, model), None)
        except ClaudeError as exc:
            return openai_error(502, f"claude failed: {exc}", "upstream_error")

        if not body.get("stream"):
            try:
                text, result = await collect(events)
            except ClaudeError as exc:
                return openai_error(502, f"claude failed: {exc}", "upstream_error")
            return {
                "id": completion_id, "object": "chat.completion", "created": created,
                "model": requested_model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": text}}],
                "usage": openai_usage(result.usage),
            }

        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

        def chunk(delta: dict, finish_reason: str | None = None, **extra) -> str:
            payload = {
                "id": completion_id, "object": "chat.completion.chunk", "created": created,
                "model": requested_model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                **extra,
            }
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        async def sse() -> AsyncIterator[str]:
            yield chunk({"role": "assistant", "content": ""})
            try:
                async for event in events:
                    if isinstance(event, str):
                        yield chunk({"content": event})
                    else:
                        usage = {"usage": openai_usage(event.usage)} if include_usage else {}
                        yield chunk({}, "stop", **usage)
            except ClaudeError as exc:
                error = {"message": f"claude failed: {exc}", "type": "upstream_error"}
                yield f"data: {json.dumps({'error': error})}\n\n"
            finally:
                await events.aclose()
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.post(ANTHROPIC_PATH, dependencies=[Depends(authorize)])
    async def messages(request: Request):
        try:
            body = await request.json()
            messages = anthropic_messages(body.get("system"), body["messages"])
            turn = split_turn(messages)
            tools = anthropic_tools(body.get("tools"), settings.server_tools)
            results = tool_results(body["messages"][-1])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise HTTPException(400, f"invalid request: {exc}") from None

        requested_model = body.get("model") or DEFAULT_MODEL_NAME
        message = {"id": f"msg_{uuid.uuid4().hex}", "type": "message", "role": "assistant",
                   "model": requested_model}
        # Results for a paused run go on with it. Otherwise, such as after a restart or a
        # timeout, a new run gets the calls and results in its transcript.
        run = bridge.claim(results) if results else None
        if run:
            run.resume(results)
        else:
            run = bridge.new_run(tools) if tools else None
            events = answer(turn, resolve_model(requested_model, settings.default_model), run)
            if run:
                run.events = events
        try:
            events = await reply(messages, run.events if run else events, run)
        except ClaudeError as exc:
            return anthropic_error(502, f"claude failed: {exc}")

        def tool_use(call: ToolUse) -> dict:
            return {"type": "tool_use", "id": call.id, "name": bridged_name(call.name, run.tools),
                    "input": call.input}

        if not body.get("stream"):
            text, calls, stop_reason, usage = "", [], "end_turn", {}
            try:
                async for event in events:
                    if isinstance(event, str):
                        text += event
                    elif isinstance(event, ToolUse):
                        calls.append(tool_use(event))
                    elif isinstance(event, AwaitingTools):
                        stop_reason = "tool_use"
                    else:
                        usage = event.usage
            except ClaudeError as exc:
                return anthropic_error(502, f"claude failed: {exc}")
            content = [{"type": "text", "text": text}] if text or not calls else []
            return {**message, "content": content + calls, "stop_reason": stop_reason,
                    "stop_sequence": None, "usage": anthropic_usage(usage)}

        def sse_event(payload: dict) -> str:
            return f"event: {payload['type']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        async def sse() -> AsyncIterator[str]:
            yield sse_event({"type": "message_start", "message": {
                **message, "content": [], "stop_reason": None, "stop_sequence": None,
                "usage": anthropic_usage({})}})
            index, text_open, stop_reason, usage = 0, False, "end_turn", {}
            try:
                async for event in events:
                    if isinstance(event, str):
                        if not text_open:
                            yield sse_event({"type": "content_block_start", "index": index,
                                             "content_block": {"type": "text", "text": ""}})
                            text_open = True
                        yield sse_event({"type": "content_block_delta", "index": index,
                                         "delta": {"type": "text_delta", "text": event}})
                    elif isinstance(event, ToolUse):
                        if text_open:
                            yield sse_event({"type": "content_block_stop", "index": index})
                            index, text_open = index + 1, False
                        block = tool_use(event)
                        yield sse_event({"type": "content_block_start", "index": index,
                                         "content_block": {**block, "input": {}}})
                        yield sse_event({"type": "content_block_delta", "index": index, "delta": {
                            "type": "input_json_delta", "partial_json": json.dumps(block["input"])}})
                        yield sse_event({"type": "content_block_stop", "index": index})
                        index += 1
                    elif isinstance(event, AwaitingTools):
                        stop_reason = "tool_use"
                    else:
                        usage = event.usage
                if text_open:
                    yield sse_event({"type": "content_block_stop", "index": index})
                yield sse_event({"type": "message_delta",
                                 "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                                 "usage": anthropic_usage(usage)})
                yield sse_event({"type": "message_stop"})
            except ClaudeError as exc:
                yield sse_event(anthropic_error_body(502, f"claude failed: {exc}"))
            finally:
                await events.aclose()

        return StreamingResponse(sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.post("/cloud/mcp", dependencies=[Depends(authorize)])
    async def cloud_mcp(request: Request):
        """The MCP server that starts cloud sessions, on when CLOUD_REPOS names repositories."""
        if not cloud:
            return JSONResponse({"error": "cloud sessions are off"}, status_code=404)
        answer_ = await cloud.handle(await request.json())
        return Response(status_code=202) if answer_ is None else JSONResponse(answer_)

    @app.get("/cloud/mcp")
    async def cloud_mcp_stream():
        return Response(status_code=405)

    @app.post("/mcp/{run_id}")
    async def mcp(run_id: str, request: Request):
        """The MCP server that claude reaches client tools through. Each run has its own token."""
        run = bridge.runs.get(run_id)
        expected = f"Bearer {run.token}" if run else ""
        if not run or not secrets.compare_digest(request.headers.get("authorization", ""), expected):
            return JSONResponse({"error": "unknown run"}, status_code=404)
        answer_ = await bridge.handle(run, await request.json())
        return Response(status_code=202) if answer_ is None else JSONResponse(answer_)

    @app.get("/mcp/{run_id}")
    async def mcp_stream(run_id: str):
        return Response(status_code=405)

    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
