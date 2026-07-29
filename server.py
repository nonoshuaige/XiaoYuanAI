"""FastAPI entry point for XiaoYuan AI."""

from __future__ import annotations

import os
import secrets
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from agent import get_context, get_runtime, reset_session
from config import get_model_options
from conversation_store import conversation_store
from meeting_room_tool import (
    MeetingRoomConflictError,
    MeetingRoomDraftNotFoundError,
    MeetingRoomDraftStateError,
    MeetingRoomError,
    MeetingRoomNotFoundError,
    MeetingRoomStore,
)
from people_tool import (
    DuplicatePersonError,
    PeopleStore,
    PersonNotFoundError,
)


app = FastAPI(title="小原 AI 助手")
SANDBOX_MODE = os.getenv("XIAOYUAN_SANDBOX") == "1"
people_store = PeopleStore()
meeting_room_store = MeetingRoomStore(seed_sandbox_data=SANDBOX_MODE)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)
    model: str | None = Field(default=None, min_length=1, max_length=256)
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


class EmployeeRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    employee_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=50)
    phone: str = Field(min_length=3, max_length=32)
    department: str = Field(min_length=1, max_length=80)


class MeetingRoomBookingRequest(BaseModel):
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
    confirmed: Literal[True]
    capacity: int = Field(default=5, ge=1, le=500)
    theme: str | None = Field(default=None, max_length=100)


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
    capacity: int = Field(default=5, ge=1, le=500)
    theme: str | None = Field(default=None, max_length=100)


def _require_sandbox() -> None:
    if not SANDBOX_MODE:
        raise HTTPException(status_code=404, detail="sandbox not enabled")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/employee-sandbox")
async def employee_sandbox_page():
    _require_sandbox()
    return FileResponse("static/employee-sandbox.html")


@app.get("/meeting-room-sandbox")
async def meeting_room_sandbox_page():
    _require_sandbox()
    return FileResponse("static/meeting-room-sandbox.html")


@app.get("/api/sandbox/status")
async def sandbox_status():
    _require_sandbox()
    return {
        "sandbox": True,
        "database": str(people_store.db_path),
        "destinations": [
            {
                "id": "employees",
                "label": "员工沙箱",
                "href": "/employee-sandbox",
            },
            {
                "id": "meeting-rooms",
                "label": "会议室沙箱",
                "href": "/meeting-room-sandbox",
            },
        ],
    }


@app.get("/api/sandbox/people")
async def list_sandbox_people(
    search: str | None = Query(default=None, max_length=100),
):
    _require_sandbox()
    return await run_in_threadpool(people_store.list_all, search)


@app.post("/api/sandbox/people", status_code=201)
async def create_sandbox_person(request: EmployeeRequest):
    _require_sandbox()
    try:
        return await run_in_threadpool(
            people_store.create,
            **request.model_dump(),
        )
    except DuplicatePersonError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.put("/api/sandbox/people/{employee_id}")
async def update_sandbox_person(
    employee_id: str,
    request: EmployeeRequest,
):
    _require_sandbox()
    try:
        return await run_in_threadpool(
            people_store.update,
            employee_id,
            **request.model_dump(),
        )
    except DuplicatePersonError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PersonNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete(
    "/api/sandbox/people/{employee_id}",
    status_code=204,
    response_class=Response,
)
async def delete_sandbox_person(employee_id: str):
    _require_sandbox()
    try:
        await run_in_threadpool(people_store.delete, employee_id)
    except PersonNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@app.get("/api/sandbox/meeting-rooms")
async def list_sandbox_meeting_rooms(
    floor: str | None = Query(default=None, pattern=r"^\d+$"),
    room: str | None = Query(default=None, min_length=1, max_length=80),
    date: str | None = Query(
        default=None,
        pattern=r"^\d{4}/\d{2}/\d{2}$",
    ),
    time_range: str | None = Query(
        default=None,
        alias="timeRange",
        pattern=r"^\d{2}:\d{2}-\d{2}:\d{2}$",
    ),
    capacity: int | None = Query(default=None, ge=1, le=500),
):
    _require_sandbox()
    try:
        return await run_in_threadpool(
            meeting_room_store.list_rooms,
            floor=floor,
            room_query=room,
            date=date,
            time_range=time_range,
            capacity=capacity,
        )
    except MeetingRoomError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/sandbox/meeting-room-bookings", status_code=201)
async def create_sandbox_meeting_room_booking(
    request: MeetingRoomBookingRequest,
):
    _require_sandbox()
    try:
        return await run_in_threadpool(
            meeting_room_store.create_booking,
            room_id=request.room_id,
            floor=request.floor,
            date=request.date,
            time_range=request.time_range,
            capacity=request.capacity,
            theme=request.theme,
        )
    except MeetingRoomConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MeetingRoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MeetingRoomError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
        "modelCallUrl": (
            f"/api/sessions/{response.session_id}/rounds/"
            f"{response.round_no}/model-call"
        ),
        "artifacts": list(response.artifacts),
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
    drafts = await run_in_threadpool(
        meeting_room_store.list_drafts,
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
        return await run_in_threadpool(meeting_room_store.get_draft, draft_id)
    except MeetingRoomDraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/meeting-room-booking-drafts/{draft_id}")
async def update_meeting_room_booking_draft(
    draft_id: str,
    request: MeetingRoomDraftUpdateRequest,
):
    try:
        return await run_in_threadpool(
            meeting_room_store.update_draft,
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
    capacity: int = Query(default=5, ge=1, le=500),
):
    try:
        await run_in_threadpool(meeting_room_store.get_draft, draft_id)
        result = await run_in_threadpool(
            meeting_room_store.list_rooms,
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
            meeting_room_store.confirm_draft,
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
        reset_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not existed:
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
