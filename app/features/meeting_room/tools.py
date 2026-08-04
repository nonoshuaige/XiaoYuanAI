"""Agent-facing meeting-room tools composed from external and local ports."""

from __future__ import annotations

import json
from contextvars import ContextVar, Token
from typing import Any, Protocol

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, model_validator

from app.features.meeting_room.domain import (
    DEFAULT_MEETING_CAPACITY,
    MeetingRoomConflictError,
    MeetingRoomDraftNotFoundError,
    MeetingRoomDraftStateError,
    MeetingRoomError,
    MeetingRoomNotFoundError,
    parse_time_range,
    validate_date,
    validate_floor,
)
from app.features.meeting_room.draft_store import MeetingRoomDraftStore
from app.features.meeting_room.gateway import MeetingRoomGateway
from app.integrations.mock_sandbox.client import MockSandboxError


_booking_draft_context: ContextVar[tuple[str, int] | None] = ContextVar(
    "meeting_room_booking_draft_context",
    default=None,
)


def set_booking_draft_context(session_id: str, round_no: int) -> Token:
    return _booking_draft_context.set((session_id, round_no))


def reset_booking_draft_context(token: Token) -> None:
    _booking_draft_context.reset(token)


class MeetingRoomClient(Protocol):
    def select_meet(
        self,
        *,
        floor: str | None = None,
        room_query: str | None = None,
        date: str | None = None,
        time_range: str | None = None,
        capacity: int | None = None,
        available_only: bool = False,
    ) -> dict[str, Any]: ...

    def create_draft(
        self,
        *,
        room_id: str,
        floor: str,
        date: str,
        time_range: str,
        capacity: int,
        theme: str | None,
        session_id: str | None,
        round_no: int | None,
    ) -> dict[str, Any]: ...


class MeetingRoomAgentClient:
    """Compose the external gateway with minimal local confirmation state."""

    def __init__(
        self,
        gateway: MeetingRoomGateway,
        drafts: MeetingRoomDraftStore,
    ):
        self.gateway = gateway
        self.drafts = drafts

    def select_meet(self, **kwargs) -> dict[str, Any]:
        return self.gateway.select_meet(**kwargs)

    def create_draft(self, **kwargs) -> dict[str, Any]:
        return self.drafts.create_draft(**kwargs)


class QueryMeetingRoomsInput(BaseModel):
    floor: str | None = Field(default=None, description="楼层，纯数字，例如7")
    room: str | None = Field(
        default=None,
        description="会议室名称、编号或外部roomId",
    )
    date: str | None = Field(default=None, description="日期，格式yyyy/MM/dd")
    timeRange: str | None = Field(
        default=None,
        description="时间段，格式HH:mm-HH:mm",
    )
    capacity: int | None = Field(
        default=None,
        description="参会人数，指定时段查询时默认5",
        ge=1,
    )

    @model_validator(mode="after")
    def validate_query(self) -> "QueryMeetingRoomsInput":
        if self.floor is not None:
            self.floor = validate_floor(self.floor)
        if self.room is not None:
            self.room = self.room.strip() or None
        if self.date is not None:
            self.date = validate_date(self.date)
        if self.timeRange is not None:
            parse_time_range(self.timeRange)
        if not any((self.floor, self.room, self.date, self.timeRange)):
            raise ValueError("floor、room、date或timeRange至少提供一项")
        return self


class BookMeetingRoomInput(BaseModel):
    roomId: str = Field(description="外部查询结果返回的会议室ID")
    floor: str = Field(description="楼层，纯数字，例如7")
    date: str = Field(description="预约日期，格式yyyy/MM/dd")
    timeRange: str = Field(description="预约时间段，格式HH:mm-HH:mm")
    capacity: int | None = Field(default=None, description="参会人数，默认5", ge=1)
    theme: str | None = Field(
        default=None,
        description="会议主题；不提供时由服务端生成默认主题",
    )

    @model_validator(mode="after")
    def validate_booking(self) -> "BookMeetingRoomInput":
        self.roomId = self.roomId.strip()
        if not self.roomId:
            raise ValueError("roomId不能为空")
        self.floor = validate_floor(self.floor)
        self.date = validate_date(self.date)
        parse_time_range(self.timeRange)
        if self.theme is not None:
            self.theme = self.theme.strip() or None
        return self


def create_meeting_room_tools(client: MeetingRoomClient) -> list[BaseTool]:
    @tool(
        "queryMeetingRooms",
        args_schema=QueryMeetingRoomsInput,
        description=(
            "从外部会议室系统按楼层、房间、日期或时间任一线索查询。"
            "返回占用信息、半小时时间轴和候选空闲时间；只查询，不写本地库存。"
        ),
    )
    def query_meeting_rooms(
        floor: str | None = None,
        room: str | None = None,
        date: str | None = None,
        timeRange: str | None = None,
        capacity: int | None = None,
    ) -> str:
        try:
            result = client.select_meet(
                floor=floor,
                room_query=room,
                date=date,
                time_range=timeRange,
                capacity=(
                    capacity or DEFAULT_MEETING_CAPACITY
                    if timeRange is not None
                    else None
                ),
                available_only=False,
            )
        except (MeetingRoomError, MockSandboxError) as exc:
            result = {"success": False, "message": str(exc), "rooms": []}
        return json.dumps(result, ensure_ascii=False)

    @tool(
        "bookMeetingRoom",
        args_schema=BookMeetingRoomInput,
        description=(
            "依据外部查询结果生成待用户确认的预约草稿，本工具绝不创建外部预约。"
            "用户必须在卡片中最终确认，模型不得代替用户完成最后写入。"
        ),
    )
    def book_meeting_room(
        roomId: str,
        floor: str,
        date: str,
        timeRange: str,
        capacity: int | None = None,
        theme: str | None = None,
    ) -> str:
        actual_capacity = capacity or DEFAULT_MEETING_CAPACITY
        try:
            latest = client.select_meet(
                floor=floor,
                date=date,
                time_range=timeRange,
                capacity=actual_capacity,
                available_only=False,
            )
        except (MeetingRoomError, MockSandboxError) as exc:
            return json.dumps(
                {"success": False, "message": str(exc)},
                ensure_ascii=False,
            )
        room = next(
            (
                candidate
                for candidate in latest.get("rooms", [])
                if candidate.get("roomId") == roomId
            ),
            None,
        )
        if room is None:
            return json.dumps(
                {"success": False, "message": "外部系统未找到指定会议室"},
                ensure_ascii=False,
            )
        if not room.get("available", False):
            return json.dumps(
                {
                    "success": False,
                    "message": "该会议室在当前时段已被预订或容量不足",
                },
                ensure_ascii=False,
            )
        try:
            context = _booking_draft_context.get()
            result = client.create_draft(
                room_id=roomId,
                floor=floor,
                date=date,
                time_range=timeRange,
                capacity=actual_capacity,
                theme=theme,
                session_id=context[0] if context else None,
                round_no=context[1] if context else None,
            )
            result = {
                "success": True,
                **result,
                "message": "请在预约卡片中检查参数并由你本人确认",
            }
        except MeetingRoomError as exc:
            result = {"success": False, "message": str(exc)}
        return json.dumps(result, ensure_ascii=False)

    return [query_meeting_rooms, book_meeting_room]


__all__ = [
    "DEFAULT_MEETING_CAPACITY",
    "MeetingRoomAgentClient",
    "MeetingRoomClient",
    "MeetingRoomConflictError",
    "MeetingRoomDraftNotFoundError",
    "MeetingRoomDraftStateError",
    "MeetingRoomError",
    "MeetingRoomNotFoundError",
    "create_meeting_room_tools",
    "reset_booking_draft_context",
    "set_booking_draft_context",
]
