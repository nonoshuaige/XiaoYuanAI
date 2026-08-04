"""FastAPI entry point for XiaoYuan AI."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.agent.context_window import DEFAULT_CONTEXT_WINDOW_TOKENS
from app.agent.runtime import get_context, get_runtime, reset_session
from app.agent.jobs import ChatJobManager
from app.providers.config import get_model_options
from app.persistence.conversations import (
    ConversationPendingError,
    conversation_store,
)
from app.persistence.database import mysql_health
from app.features.meeting_room.domain import (
    DEFAULT_MEETING_CAPACITY,
    MeetingRoomConflictError,
    MeetingRoomDraftNotFoundError,
    MeetingRoomDraftStateError,
    MeetingRoomError,
    MeetingRoomNotFoundError,
)
from app.features.meeting_room.draft_store import MeetingRoomDraftStore
from app.features.meeting_room.gateway import MockSandboxMeetingRoomGateway
from app.features.current_user.service import (
    CurrentUserNotFoundError,
    CurrentUserService,
)
from app.features.people.tools import MockSandboxPeopleClient
from app.integrations.mock_sandbox.client import (
    MockSandboxError,
    get_mock_sandbox_client,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIST_DIR = Path(
    os.getenv("XIAOYUAN_FRONTEND_DIST", PROJECT_DIR / "frontend" / "dist")
).expanduser()
if not FRONTEND_DIST_DIR.is_absolute():
    FRONTEND_DIST_DIR = (PROJECT_DIR / FRONTEND_DIST_DIR).resolve()
FRONTEND_INDEX_PATH = FRONTEND_DIST_DIR / "index.html"
FRONTEND_ASSETS_DIR = FRONTEND_DIST_DIR / "assets"
FRONTEND_FAVICON_PATH = FRONTEND_DIST_DIR / "favicon.svg"

app = FastAPI(title="小原 AI 助手")
CHAT_JOB_OWNER = "normal"
mock_sandbox_http = get_mock_sandbox_client()
meeting_room_gateway = MockSandboxMeetingRoomGateway(mock_sandbox_http)
meeting_room_drafts = MeetingRoomDraftStore(
    meeting_room_gateway,
    booked_by=mock_sandbox_http.settings.user_name,
    booked_by_provider=lambda: mock_sandbox_http.settings.user_name,
)
current_user_service = CurrentUserService(
    mock_sandbox_http,
    MockSandboxPeopleClient(mock_sandbox_http),
)


def _complete_chat_job(
    session_id: str,
    round_no: int,
    model_id: str | None,
):
    return get_runtime().complete_chat(session_id, round_no, model_id)


chat_job_manager = ChatJobManager(_complete_chat_job)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount(
    "/assets",
    StaticFiles(directory=FRONTEND_ASSETS_DIR, check_dir=False),
    name="frontend-assets",
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    context_window_tokens: int = Field(
        default=DEFAULT_CONTEXT_WINDOW_TOKENS,
        validation_alias=AliasChoices(
            "contextWindowTokens",
            "context_window_tokens",
        ),
    )
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


class CurrentUserRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, populate_by_name=True)

    employee_id: str = Field(
        alias="employeeId",
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class MeetingRoomDraftUpdateRequest(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    room_id: str = Field(alias="roomId", min_length=1, max_length=80)
    floor: str = Field(min_length=1, max_length=8, pattern=r"^\d+$")
    date: str = Field(pattern=r"^\d{4}/\d{2}/\d{2}$")
    time_range: str = Field(
        alias="timeRange",
        pattern=r"^\d{2}:\d{2}-\d{2}:\d{2}$",
    )
    capacity: int = Field(
        default=DEFAULT_MEETING_CAPACITY,
        ge=1,
        le=500,
    )
    theme: str | None = Field(default=None, max_length=100)


def _frontend_page() -> FileResponse:
    if not FRONTEND_INDEX_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail=(
                "前端尚未构建，请先在 frontend 目录运行 "
                "`npm install` 和 `npm run build`"
            ),
        )
    return FileResponse(FRONTEND_INDEX_PATH)


@app.get("/")
async def index():
    return _frontend_page()


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


@app.get("/ready", include_in_schema=False)
async def ready():
    try:
        database = await run_in_threadpool(mysql_health)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="MySQL unavailable") from exc
    try:
        mock_sandbox = await run_in_threadpool(
            mock_sandbox_http.request_json,
            "GET",
            "/api/ready",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Mock Sandbox unavailable",
        ) from exc
    return {
        "status": "ok",
        "checks": {
            "database": database,
            "mockSandbox": mock_sandbox,
        },
    }


@app.get("/favicon.svg", include_in_schema=False)
async def favicon():
    if not FRONTEND_FAVICON_PATH.is_file():
        raise HTTPException(status_code=404, detail="favicon not built")
    return FileResponse(FRONTEND_FAVICON_PATH, media_type="image/svg+xml")


@app.get("/api/sessions")
async def list_sessions():
    return conversation_store.list_sessions()


@app.get("/api/models")
async def list_models(refresh: bool = False):
    return await run_in_threadpool(get_model_options, refresh=refresh)


@app.get("/api/current-user")
async def get_current_user():
    return current_user_service.current().as_dict()


@app.post("/api/current-user/resolve")
async def resolve_current_user(request: CurrentUserRequest):
    return await _resolve_current_user(request.employee_id, switch=False)


@app.put("/api/current-user")
async def switch_current_user(request: CurrentUserRequest):
    return await _resolve_current_user(request.employee_id, switch=True)


async def _resolve_current_user(employee_id: str, *, switch: bool):
    action = current_user_service.switch if switch else current_user_service.resolve
    try:
        user = await run_in_threadpool(action, employee_id)
    except CurrentUserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MockSandboxError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return user.as_dict()


@app.on_event("startup")
async def resume_pending_chat_jobs():
    """Resume recently persisted turns after a service restart."""
    pending_rounds = conversation_store.list_pending_rounds(
        job_owner=CHAT_JOB_OWNER
    )
    pending_rounds.extend(
        conversation_store.list_pending_rounds(job_owner="default")
    )
    for pending in pending_rounds:
        chat_job_manager.submit(
            pending["sessionId"],
            pending["round"],
            pending["modelId"],
        )


@app.post("/api/chat", status_code=202)
async def chat_endpoint(request: ChatRequest):
    session_id = request.session_id or secrets.token_hex(8)
    try:
        round_no, model_id = get_runtime().begin_chat(
            session_id,
            request.message,
            request.model,
            job_owner=CHAT_JOB_OWNER,
            context_window_tokens=request.context_window_tokens,
        )
        chat_job_manager.submit(session_id, round_no, model_id)
    except ConversationPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    session = conversation_store.get_session(session_id)
    return {
        "reply": "",
        "sessionId": session_id,
        "round": round_no,
        "status": "pending",
        "title": session["title"],
        "model": model_id,
        "contextWindowTokens": request.context_window_tokens,
        "modelCallUrl": (
            f"/api/sessions/{session_id}/rounds/"
            f"{round_no}/model-call"
        ),
        "eventsUrl": (
            f"/api/sessions/{session_id}/rounds/"
            f"{round_no}/events"
        ),
        "artifacts": [],
        "quickReplies": [],
    }


@app.get("/api/sessions/{session_id}/rounds/{round_no}/events")
async def stream_chat_events(
    session_id: str,
    round_no: int,
    request: Request,
    after: int = Query(default=0, ge=0),
):
    """Replay and follow one durable Agent turn as an SSE stream."""
    try:
        round_state = conversation_store.get_round(session_id, round_no)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if round_state is None:
        raise HTTPException(status_code=404, detail="round not found")

    header_cursor = request.headers.get("last-event-id", "").strip()
    cursor = after
    if header_cursor:
        try:
            cursor = max(cursor, int(header_cursor))
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Last-Event-ID 必须是整数",
            ) from exc

    async def event_stream():
        nonlocal cursor
        last_heartbeat = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            events = await run_in_threadpool(
                conversation_store.get_chat_events,
                session_id,
                round_no,
                after_event_id=cursor,
            )
            if events:
                for event in events:
                    cursor = event["eventId"]
                    yield (
                        f"id: {cursor}\n"
                        f"event: {event['type']}\n"
                        f"data: {json.dumps(event['payload'], ensure_ascii=False)}\n\n"
                    )
                    if event["type"] in {"completed", "failed"}:
                        return
                last_heartbeat = time.monotonic()
                continue
            if time.monotonic() - last_heartbeat >= 10:
                yield ": heartbeat\n\n"
                last_heartbeat = time.monotonic()
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/sessions/{session_id}")
async def session_context(session_id: str):
    try:
        if conversation_store.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        context = get_context(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    drafts = await run_in_threadpool(
        meeting_room_drafts.list_drafts,
        session_id=session_id,
    )
    artifacts_by_round: dict[int, list[dict]] = {}
    for draft in drafts:
        if draft["round"] is not None:
            artifacts_by_round.setdefault(int(draft["round"]), []).append(
                draft
            )
    return {
        "sessionId": session_id,
        **context,
        "artifactsByRound": artifacts_by_round,
    }


@app.get("/api/meeting-room-booking-drafts/{draft_id}")
async def get_meeting_room_booking_draft(draft_id: str):
    try:
        return await run_in_threadpool(meeting_room_drafts.get_draft, draft_id)
    except MeetingRoomDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/meeting-room-booking-drafts/{draft_id}")
async def update_meeting_room_booking_draft(
    draft_id: str,
    request: MeetingRoomDraftUpdateRequest,
):
    try:
        return await run_in_threadpool(
            meeting_room_drafts.update_draft,
            draft_id,
            room_id=request.room_id,
            floor=request.floor,
            date=request.date,
            time_range=request.time_range,
            capacity=request.capacity,
            theme=request.theme,
        )
    except MeetingRoomDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MeetingRoomConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MeetingRoomDraftStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MeetingRoomError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get(
    "/api/meeting-room-booking-drafts/{draft_id}/room-options"
)
async def meeting_room_booking_draft_options(
    draft_id: str,
    floor: str = Query(pattern=r"^\d+$"),
    date: str = Query(pattern=r"^\d{4}/\d{2}/\d{2}$"),
    time_range: str = Query(
        alias="timeRange",
        pattern=r"^\d{2}:\d{2}-\d{2}:\d{2}$",
    ),
    capacity: int = Query(
        default=DEFAULT_MEETING_CAPACITY,
        ge=1,
        le=500,
    ),
):
    try:
        await run_in_threadpool(meeting_room_drafts.get_draft, draft_id)
        result = await run_in_threadpool(
            meeting_room_drafts.list_rooms,
            floor=floor,
            date=date,
            time_range=time_range,
            capacity=capacity,
        )
        return {"rooms": result["rooms"]}
    except MeetingRoomDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MeetingRoomError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/api/meeting-room-booking-drafts/{draft_id}/confirm"
)
async def confirm_meeting_room_booking_draft(draft_id: str):
    try:
        return await run_in_threadpool(
            meeting_room_drafts.confirm_draft,
            draft_id,
        )
    except MeetingRoomDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (
        MeetingRoomConflictError,
        MeetingRoomDraftStateError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MeetingRoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MeetingRoomError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/api/meeting-room-booking-drafts/{draft_id}/cancel"
)
async def cancel_meeting_room_booking_draft(draft_id: str):
    try:
        return await run_in_threadpool(
            meeting_room_drafts.cancel_draft,
            draft_id,
        )
    except MeetingRoomDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MeetingRoomDraftStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/sessions/{session_id}/model-calls")
async def session_model_calls(session_id: str):
    """Return the full model-call audit trail for one session."""
    try:
        if conversation_store.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        return conversation_store.get_model_calls(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/sessions/{session_id}/rounds/{round_no}/model-call")
async def round_model_call(session_id: str, round_no: int):
    """Return Provider HTTP data and the converted LangChain AIMessage."""
    try:
        if conversation_store.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        model_calls = conversation_store.get_model_calls(
            session_id,
            round_no=round_no,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not model_calls:
        raise HTTPException(status_code=404, detail="model call audit not found")
    return model_calls[0]


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
        latest_round = conversation_store.latest_round(session_id)
        latest_state = (
            conversation_store.get_round(session_id, latest_round)
            if latest_round
            else None
        )
        if latest_state and latest_state["status"] == "pending":
            raise HTTPException(
                status_code=409,
                detail="会话正在后台生成，完成后才能删除",
            )
        reset_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not existed:
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
