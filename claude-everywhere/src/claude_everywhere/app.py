"""OpenAI Chat Completions API that answers with `claude -p`, one Claude session per conversation."""

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

from .core import DEFAULT_MODEL_NAME, MODEL_ALIASES, Turn, normalize, reply_key, resolve_model, split_turn, transcript_prompt
from .runner import ClaudeError, RunResult, run_claude
from .sessions import SessionStore
from .settings import Settings

log = logging.getLogger("claude_everywhere")
MODELS = [DEFAULT_MODEL_NAME, *sorted(MODEL_ALIASES)]


def openai_error(status: int, message: str, kind: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def openai_usage(usage: dict) -> dict:
    prompt = sum(usage.get(k, 0) for k in
                 ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    completion = usage.get("output_tokens", 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion}


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
    async def http_error(_: Request, exc: HTTPException):
        return openai_error(exc.status_code, str(exc.detail), "invalid_request_error")

    def authorize(request: Request) -> None:
        if settings.api_key and request.headers.get("authorization") != f"Bearer {settings.api_key}":
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

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/models", dependencies=[Depends(authorize)])
    async def models():
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
        model = resolve_model(requested_model, settings.default_model)
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        events = answer(turn, model)

        def remember(result: RunResult) -> None:
            if result.session_id:
                store.put(reply_key(messages, result.text), result.session_id)

        # The first event comes before the response, so a failed start is an HTTP error.
        try:
            first = await anext(events)
        except ClaudeError as exc:
            return openai_error(502, f"claude failed: {exc}", "upstream_error")

        if not body.get("stream"):
            text = ""
            result = first if isinstance(first, RunResult) else None
            if isinstance(first, str):
                text = first
            try:
                async for event in events:
                    if isinstance(event, str):
                        text += event
                    else:
                        result = event
            except ClaudeError as exc:
                return openai_error(502, f"claude failed: {exc}", "upstream_error")
            remember(result)
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
                event = first
                while True:
                    if isinstance(event, str):
                        yield chunk({"content": event})
                    else:
                        remember(event)
                        usage = {"usage": openai_usage(event.usage)} if include_usage else {}
                        yield chunk({}, "stop", **usage)
                    event = await anext(events, None)
                    if event is None:
                        break
            except ClaudeError as exc:
                error = {"message": f"claude failed: {exc}", "type": "upstream_error"}
                yield f"data: {json.dumps({'error': error})}\n\n"
            finally:
                await events.aclose()
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
