"""OpenAI Chat Completions and Anthropic Messages APIs that answer with `claude -p`,
one Claude session per conversation."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .core import (
    DEFAULT_MODEL_NAME, MODEL_ALIASES, Message, Turn, anthropic_messages, normalize, reply_key,
    resolve_model, split_turn, transcript_prompt,
)
from .runner import ClaudeError, RunResult, run_claude
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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
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

    async def answer(turn: Turn, model: str | None) -> AsyncIterator[str | RunResult]:
        """Resumes the conversation's session when known, else starts one from a transcript."""
        async with slots:
            session = store.get(turn.parent_key) if turn.history else None
            if session:
                started = False
                try:
                    async for event in run_claude(settings, turn.prompt, resume=session, model=model):
                        started = True
                        yield event
                    return
                except ClaudeError as exc:
                    if started:
                        raise
                    log.warning("resuming session %s failed, starting a new one: %s", session, exc)
            prompt = transcript_prompt(turn.history, turn.prompt)
            async for event in run_claude(settings, prompt, system=turn.system or None, model=model):
                yield event

    async def start(
        messages: list[Message], turn: Turn, model: str | None
    ) -> AsyncIterator[str | RunResult]:
        """Runs the turn up to its first event, so a failed start raises ClaudeError before
        any response. The events that follow store the reply's session."""
        events = answer(turn, model)
        first = await anext(events)

        async def rest() -> AsyncIterator[str | RunResult]:
            try:
                event = first
                while event is not None:
                    if isinstance(event, RunResult) and event.session_id:
                        store.put(reply_key(messages, event.text), event.session_id)
                    yield event
                    event = await anext(events, None)
            finally:
                await events.aclose()

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
        try:
            events = await start(messages, turn, resolve_model(requested_model, settings.default_model))
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
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise HTTPException(400, f"invalid request: {exc}") from None

        requested_model = body.get("model") or DEFAULT_MODEL_NAME
        message = {"id": f"msg_{uuid.uuid4().hex}", "type": "message", "role": "assistant",
                   "model": requested_model}
        try:
            events = await start(messages, turn, resolve_model(requested_model, settings.default_model))
        except ClaudeError as exc:
            return anthropic_error(502, f"claude failed: {exc}")

        if not body.get("stream"):
            try:
                text, result = await collect(events)
            except ClaudeError as exc:
                return anthropic_error(502, f"claude failed: {exc}")
            return {**message, "content": [{"type": "text", "text": text}],
                    "stop_reason": "end_turn", "stop_sequence": None,
                    "usage": anthropic_usage(result.usage)}

        def sse_event(payload: dict) -> str:
            return f"event: {payload['type']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        async def sse() -> AsyncIterator[str]:
            yield sse_event({"type": "message_start", "message": {
                **message, "content": [], "stop_reason": None, "stop_sequence": None,
                "usage": anthropic_usage({})}})
            yield sse_event({"type": "content_block_start", "index": 0,
                             "content_block": {"type": "text", "text": ""}})
            try:
                async for event in events:
                    if isinstance(event, str):
                        yield sse_event({"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": event}})
                    else:
                        yield sse_event({"type": "content_block_stop", "index": 0})
                        yield sse_event({"type": "message_delta",
                                         "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                         "usage": anthropic_usage(event.usage)})
                        yield sse_event({"type": "message_stop"})
            except ClaudeError as exc:
                yield sse_event(anthropic_error_body(502, f"claude failed: {exc}"))
            finally:
                await events.aclose()

        return StreamingResponse(sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
