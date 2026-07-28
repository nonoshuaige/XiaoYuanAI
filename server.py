"""FastAPI entry point for XiaoYuan AI."""

from __future__ import annotations

import secrets

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AliasChoices, BaseModel, Field

from agent import get_context, get_runtime, reset_session
from config import get_model_options
from conversation_store import conversation_store


app = FastAPI(title="小原 AI 助手")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)
    model: str | None = Field(default=None, min_length=1, max_length=64)
    session_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "sessionId",
            "session_id",
            "conversation_id",
        ),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class RenameSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/api/sessions")
async def list_sessions():
    return conversation_store.list_sessions()


@app.get("/api/models")
async def list_models(refresh: bool = False):
    return await run_in_threadpool(get_model_options, refresh=refresh)


@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    session_id = request.session_id or secrets.token_hex(8)
    try:
        response = await run_in_threadpool(
            _chat,
            session_id,
            request.message,
            request.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "reply": response.reply,
        "sessionId": response.session_id,
        "round": response.round_no,
        "title": conversation_store.get_session(response.session_id)["title"],
        "model": response.model_id,
    }


def _chat(
    session_id: str,
    message: str,
    model_id: str | None,
):
    """Keep first-use provider discovery and model loading off the event loop."""
    return get_runtime().chat(session_id, message, model_id)


@app.get("/api/sessions/{session_id}")
async def session_context(session_id: str):
    try:
        if conversation_store.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        context = get_context(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"sessionId": session_id, **context}


@app.patch("/api/sessions/{session_id}")
async def rename_session(
    session_id: str,
    request: RenameSessionRequest,
):
    try:
        session = conversation_store.rename_session(session_id, request.title)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.delete("/api/sessions/{session_id}")
async def clear_session(session_id: str):
    try:
        existed = conversation_store.get_session(session_id) is not None
        reset_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not existed:
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
