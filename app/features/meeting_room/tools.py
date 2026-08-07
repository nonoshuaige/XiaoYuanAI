"""Agent-facing meeting-room tools composed from external and local ports."""

from __future__ import annotations

import json
import re
import threading
from contextvars import ContextVar, Token
from typing import Any, Callable, Protocol

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.features.meeting_room.domain import (
    DEFAULT_MEETING_CAPACITY,
    DEFAULT_MEETING_DURATION_MINUTES,
    SLOT_MINUTES,
    WORKDAY_END_MINUTES,
    WORKDAY_START_MINUTES,
    MeetingRoomConflictError,
    MeetingRoomDraftNotFoundError,
    MeetingRoomDraftStateError,
    MeetingRoomError,
    MeetingRoomNotFoundError,
    overlaps,
    parse_time_range,
    time_to_minutes,
    validate_date,
    validate_floor,
)
from app.features.meeting_room.draft_store import MeetingRoomDraftStore
from app.features.meeting_room.gateway import MeetingRoomGateway
from app.integrations.mock_sandbox.client import MockSandboxError


MAX_PARALLEL_FLOOR_QUERIES = 5

_booking_draft_context: ContextVar[tuple[str, int] | None] = ContextVar(
    "meeting_room_booking_draft_context",
    default=None,
)
_floor_query_slots = threading.BoundedSemaphore(MAX_PARALLEL_FLOOR_QUERIES)


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


class ToolInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FloorInput(ToolInputModel):
    floor: int = Field(ge=1, description="楼层，如8或11")

    @model_validator(mode="after")
    def validate_floor_value(self) -> "FloorInput":
        self.floor = int(validate_floor(str(self.floor)))
        return self


class MeetingRoomDateInput(ToolInputModel):
    date: str | None = Field(
        default=None,
        description="日期，格式yyyy/MM/dd；省略时默认今天",
    )

    @model_validator(mode="after")
    def validate_date_value(self) -> "MeetingRoomDateInput":
        if self.date is not None:
            self.date = validate_date(self.date)
        return self


class SearchMeetingRoomsTimeInput(MeetingRoomDateInput):
    start: str | None = Field(
        default=None,
        description="过滤开始时间，格式HH:mm；与end同时提供",
    )
    end: str | None = Field(
        default=None,
        description="过滤结束时间，格式HH:mm；与start同时提供",
    )

    @model_validator(mode="after")
    def validate_window(self) -> "SearchMeetingRoomsTimeInput":
        if (self.start is None) != (self.end is None):
            raise ValueError("start和end必须同时提供或同时省略")
        if self.start is not None and self.end is not None:
            self.start, self.end = parse_time_range(f"{self.start}-{self.end}")
            if (
                time_to_minutes(self.start) < WORKDAY_START_MINUTES
                or time_to_minutes(self.end) > WORKDAY_END_MINUTES
            ):
                raise ValueError("查询时段必须在09:00-18:30内")
        return self

    @property
    def has_window(self) -> bool:
        return self.start is not None and self.end is not None


class MeetingRoomTimeInput(MeetingRoomDateInput):
    start: str = Field(description="开始时间，格式HH:mm")
    end: str = Field(description="结束时间，格式HH:mm")

    @model_validator(mode="after")
    def validate_time(self) -> "MeetingRoomTimeInput":
        self.start, self.end = parse_time_range(f"{self.start}-{self.end}")
        return self

    @property
    def time_range(self) -> str:
        return f"{self.start}-{self.end}"


class MeetingRoomRequirementsInput(ToolInputModel):
    minCapacity: int | None = Field(
        default=None,
        ge=1,
        description="最小容纳人数；省略时不过滤容量",
    )
    equipment: list[str] = Field(
        default_factory=list,
        description="必须全部具备的设备名称；省略时不过滤设备",
    )
    availableOnly: bool = Field(
        default=False,
        description="是否只返回指定时段完整可用的会议室",
    )

    @model_validator(mode="after")
    def normalize_equipment(self) -> "MeetingRoomRequirementsInput":
        normalized: list[str] = []
        for item in self.equipment:
            value = item.strip()
            if not value:
                raise ValueError("设备名称不能为空")
            if value not in normalized:
                normalized.append(value)
        self.equipment = normalized
        return self

    @property
    def has_static_constraints(self) -> bool:
        return self.minCapacity is not None or bool(self.equipment)


class SearchMeetingRoomsInput(FloorInput):
    roomQuery: str | None = Field(
        default=None,
        max_length=128,
        description="可选的会议室名称、房间号或查询结果中的roomId；按精确身份过滤",
    )
    time: SearchMeetingRoomsTimeInput | None = Field(
        default=None,
        description="可选日期与时段；传空对象表示查询今天完整时间表",
    )
    requirements: MeetingRoomRequirementsInput | None = Field(
        default=None,
        description="可选的容量、设备和仅看空闲等约束",
    )

    @model_validator(mode="after")
    def validate_search(self) -> "SearchMeetingRoomsInput":
        if self.roomQuery is not None:
            self.roomQuery = self.roomQuery.strip()
            if not self.roomQuery:
                raise ValueError("roomQuery不能为空")
        if (
            self.requirements is not None
            and self.requirements.availableOnly
            and (self.time is None or not self.time.has_window)
        ):
            raise ValueError("availableOnly需要同时提供准确的start和end")
        return self


class BookMeetingRoomInput(ToolInputModel):
    roomId: str = Field(description="查询结果中的外部会议室ID")
    time: MeetingRoomTimeInput = Field(description="预约日期和准确时段")
    capacity: int = Field(
        default=DEFAULT_MEETING_CAPACITY,
        ge=1,
        description="参会人数，默认5",
    )

    @model_validator(mode="after")
    def validate_booking(self) -> "BookMeetingRoomInput":
        self.roomId = self.roomId.strip()
        if not self.roomId:
            raise ValueError("roomId不能为空")
        if any(
            time_to_minutes(value) % SLOT_MINUTES
            for value in (self.time.start, self.time.end)
        ):
            raise ValueError("预约时间必须按30分钟整点或半点提供")
        return self


def _select_meet(client: MeetingRoomClient, **kwargs: Any) -> dict[str, Any]:
    """Keep concurrent floor reads within the runtime's hard limit."""
    with _floor_query_slots:
        return client.select_meet(**kwargs)


def _matching_booking(
    room: dict[str, Any],
    start: str,
    end: str,
) -> dict[str, Any] | None:
    for booking in room.get("occupied") or []:
        try:
            booking_start, booking_end = parse_time_range(
                str(booking.get("timeRange") or "")
            )
        except MeetingRoomError:
            continue
        if overlaps(start, end, booking_start, booking_end):
            return booking
    for slot in room.get("timeline") or []:
        booking = slot.get("booking")
        if not isinstance(booking, dict):
            continue
        try:
            booking_start, booking_end = parse_time_range(
                str(booking.get("timeRange") or "")
            )
        except MeetingRoomError:
            continue
        if overlaps(start, end, booking_start, booking_end):
            return booking
    return None


def _timeline_slot(slot: dict[str, Any]) -> dict[str, Any]:
    booking = slot.get("booking")
    if not isinstance(booking, dict):
        booking = None
    available = bool(slot.get("available"))
    return {
        "start": slot.get("start"),
        "end": slot.get("end"),
        "timeRange": slot.get("timeRange"),
        "available": available,
        "status": slot.get("status") or ("available" if available else "occupied"),
        "occupiedBy": booking.get("bookedBy") if booking else None,
        "occupiedTimeRange": booking.get("timeRange") if booking else None,
    }


def _available_time_ranges(timeline: list[dict[str, Any]]) -> list[str]:
    ranges: list[str] = []
    range_start: str | None = None
    previous_end: str | None = None
    for slot in timeline:
        start = str(slot.get("start") or "")
        end = str(slot.get("end") or "")
        if slot.get("available") and (range_start is None or start == previous_end):
            range_start = range_start or start
        else:
            if range_start is not None and previous_end is not None:
                ranges.append(f"{range_start}-{previous_end}")
            range_start = start if slot.get("available") else None
        previous_end = end
    if range_start is not None and previous_end is not None:
        ranges.append(f"{range_start}-{previous_end}")
    return ranges


def _normalized_room_key(value: Any) -> str:
    normalized = re.sub(r"\s+", "", str(value or "")).casefold()
    return normalized.removesuffix("会议室")


def _filter_floor_identity(
    rooms: list[dict[str, Any]],
    query: SearchMeetingRoomsInput,
) -> list[dict[str, Any]]:
    expected = str(query.floor)
    return [
        room
        for room in rooms
        if str(room.get("floor") or "").strip().rstrip("Ff层") == expected
    ]


def _filter_room_identity(
    rooms: list[dict[str, Any]],
    query: SearchMeetingRoomsInput,
) -> list[dict[str, Any]]:
    if query.roomQuery is None:
        return rooms
    expected_id = query.roomQuery.casefold()
    expected_name = _normalized_room_key(query.roomQuery)
    return [
        room
        for room in rooms
        if str(room.get("roomId") or "").casefold() == expected_id
        or _normalized_room_key(room.get("roomName")) == expected_name
    ]


def _filter_min_capacity(
    rooms: list[dict[str, Any]],
    query: SearchMeetingRoomsInput,
) -> list[dict[str, Any]]:
    requirements = query.requirements
    if requirements is None or requirements.minCapacity is None:
        return rooms
    return [
        room
        for room in rooms
        if int(room.get("capacity") or 0) >= requirements.minCapacity
    ]


def _normalized_equipment(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _filter_equipment(
    rooms: list[dict[str, Any]],
    query: SearchMeetingRoomsInput,
) -> list[dict[str, Any]]:
    requirements = query.requirements
    if requirements is None or not requirements.equipment:
        return rooms
    expected = {_normalized_equipment(item) for item in requirements.equipment}
    return [
        room
        for room in rooms
        if expected.issubset(
            {_normalized_equipment(item) for item in room.get("equipment") or []}
        )
    ]


def _window_is_available(
    timeline: list[dict[str, Any]],
    *,
    start: str,
    end: str,
) -> bool:
    relevant = [
        slot
        for slot in timeline
        if overlaps(
            str(slot.get("start") or ""),
            str(slot.get("end") or ""),
            start,
            end,
        )
    ]
    if not relevant or str(relevant[0].get("start") or "") > start:
        return False
    covered_until = start
    for slot in relevant:
        slot_start = str(slot.get("start") or "")
        slot_end = str(slot.get("end") or "")
        if slot_start > covered_until or not slot.get("available"):
            return False
        if slot_end > covered_until:
            covered_until = slot_end
    return covered_until >= end


def _window_conflicts(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for slot in timeline:
        if slot.get("available"):
            continue
        identity = (slot.get("occupiedBy"), slot.get("occupiedTimeRange"))
        if identity in seen:
            continue
        seen.add(identity)
        conflicts.append(
            {
                "occupiedBy": slot.get("occupiedBy"),
                "timeRange": slot.get("occupiedTimeRange")
                or slot.get("timeRange"),
            }
        )
    return conflicts


def _evaluate_schedule(
    rooms: list[dict[str, Any]],
    query: SearchMeetingRoomsInput,
) -> list[dict[str, Any]]:
    time_filter = query.time
    if time_filter is None:
        return rooms
    evaluated: list[dict[str, Any]] = []
    for room in rooms:
        raw_timeline = [_timeline_slot(slot) for slot in room.get("timeline") or []]
        timeline = []
        for slot in raw_timeline:
            start = str(slot.get("start") or "")
            end = str(slot.get("end") or "")
            if time_filter.has_window and not overlaps(
                start,
                end,
                str(time_filter.start),
                str(time_filter.end),
            ):
                continue
            timeline.append(slot)
        enriched = {
            **room,
            "timeline": timeline,
            "availableTimeRanges": _available_time_ranges(timeline),
        }
        if time_filter.has_window:
            enriched["isAvailable"] = _window_is_available(
                raw_timeline,
                start=str(time_filter.start),
                end=str(time_filter.end),
            )
            enriched["conflicts"] = _window_conflicts(timeline)
        evaluated.append(enriched)
    return evaluated


def _filter_available_only(
    rooms: list[dict[str, Any]],
    query: SearchMeetingRoomsInput,
) -> list[dict[str, Any]]:
    requirements = query.requirements
    if requirements is None or not requirements.availableOnly:
        return rooms
    return [room for room in rooms if room.get("isAvailable") is True]


SearchPipelineStage = Callable[
    [list[dict[str, Any]], SearchMeetingRoomsInput],
    list[dict[str, Any]],
]

_SEARCH_PIPELINE: tuple[tuple[str, SearchPipelineStage], ...] = (
    ("floor_has_no_rooms", _filter_floor_identity),
    ("room_not_found_on_floor", _filter_room_identity),
    ("no_room_meets_capacity", _filter_min_capacity),
    ("no_room_has_required_equipment", _filter_equipment),
    ("", _evaluate_schedule),
    ("no_room_available_in_time", _filter_available_only),
)


def _project_search_room(
    room: dict[str, Any],
    query: SearchMeetingRoomsInput,
) -> dict[str, Any]:
    projected = {
        "roomId": room.get("roomId"),
        "roomName": room.get("roomName"),
        "floor": room.get("floor"),
    }
    requirements = query.requirements
    if query.time is None or (
        requirements is not None and requirements.has_static_constraints
    ):
        projected.update(
            {
                "capacity": room.get("capacity"),
                "equipment": room.get("equipment") or [],
            }
        )
    if query.time is not None:
        projected.update(
            {
                "timeline": room.get("timeline") or [],
                "availableTimeRanges": room.get("availableTimeRanges") or [],
            }
        )
        if query.time.has_window:
            projected.update(
                {
                    "isAvailable": bool(room.get("isAvailable")),
                    "conflicts": room.get("conflicts") or [],
                }
            )
    return projected


def _conflict_result(
    room: dict[str, Any],
    *,
    start: str,
    end: str,
) -> dict[str, Any]:
    booking = _matching_booking(room, start, end)
    return {
        "success": False,
        "conflict": True,
        "occupiedBy": booking.get("bookedBy") if booking else None,
        "occupiedTimeRange": booking.get("timeRange") if booking else None,
        "message": "该会议室在当前时段已被占用",
    }


def create_meeting_room_tools(client: MeetingRoomClient) -> list[BaseTool]:
    @tool(
        "search_meeting_rooms",
        args_schema=SearchMeetingRoomsInput,
        description=(
            "查询单楼层会议室。可按房间名称/roomId、日期时段、最小容量、设备和空闲状态"
            "逐层筛选；未传time时返回静态信息，传time时返回相应日程。零结果通过emptyReason"
            "说明首个未满足约束，一次调用完成复合查询。"
        ),
    )
    def search_meeting_rooms(
        floor: int,
        roomQuery: str | None = None,
        time: SearchMeetingRoomsTimeInput | None = None,
        requirements: MeetingRoomRequirementsInput | None = None,
    ) -> str:
        try:
            query = SearchMeetingRoomsInput(
                floor=floor,
                roomQuery=roomQuery,
                time=time,
                requirements=requirements,
            )
            normalized_floor = validate_floor(str(query.floor))
            raw = _select_meet(
                client,
                floor=normalized_floor,
                room_query=None,
                date=query.time.date if query.time else None,
                time_range=None,
                capacity=None,
                available_only=False,
            )
            rooms = [dict(room) for room in raw.get("rooms") or []]
            empty_reason = "floor_has_no_rooms" if not rooms else None
            for reason, stage in _SEARCH_PIPELINE:
                had_candidates = bool(rooms)
                rooms = stage(rooms, query)
                if had_candidates and not rooms and reason:
                    empty_reason = reason
                    break
            result = {
                "success": True,
                "source": raw.get("source"),
                "observedAt": raw.get("observedAt"),
                "floor": f"{normalized_floor}F",
                "matchedRoomCount": len(rooms),
                "rooms": [_project_search_room(room, query) for room in rooms],
            }
            if not rooms:
                result["emptyReason"] = empty_reason or "no_matching_rooms"
            if query.roomQuery is not None:
                result["roomQuery"] = query.roomQuery
            if query.time is not None:
                result.update(
                    {
                        "date": raw.get("date"),
                        "time": {
                            "date": raw.get("date"),
                            "start": query.time.start,
                            "end": query.time.end,
                        },
                    }
                )
            if query.requirements is not None:
                applied_requirements = query.requirements.model_dump(
                    exclude_defaults=True,
                    exclude_none=True,
                )
                if applied_requirements:
                    result["requirements"] = applied_requirements
        except (MeetingRoomError, MockSandboxError) as exc:
            result = {
                "success": False,
                "message": str(exc),
                "matchedRoomCount": 0,
                "rooms": [],
            }
        return json.dumps(result, ensure_ascii=False)

    @tool(
        "book_meeting_room",
        args_schema=BookMeetingRoomInput,
        description=(
            "重查指定roomId、日期和时段；冲突返回占用信息，空闲创建待确认草稿，"
            "不执行真实预约。"
        ),
    )
    def book_meeting_room(
        roomId: str,
        time: MeetingRoomTimeInput,
        capacity: int = DEFAULT_MEETING_CAPACITY,
    ) -> str:
        actual_capacity = capacity
        try:
            room_lookup = _select_meet(
                client,
                floor=None,
                room_query=roomId,
                date=time.date,
                time_range=None,
                capacity=None,
                available_only=False,
            )
            room = next(
                (
                    candidate
                    for candidate in room_lookup.get("rooms") or []
                    if candidate.get("roomId") == roomId
                ),
                None,
            )
            if room is None:
                return json.dumps(
                    {"success": False, "message": "外部系统未找到指定会议室"},
                    ensure_ascii=False,
                )
            resolved_floor = validate_floor(
                str(room.get("floor") or "").strip().rstrip("Ff层")
            )
            resolved_date = validate_date(
                str(room_lookup.get("date") or time.date or "")
            )
            latest = _select_meet(
                client,
                floor=resolved_floor,
                room_query=roomId,
                date=resolved_date,
                time_range=time.time_range,
                capacity=actual_capacity,
                available_only=False,
            )
            latest_room = next(
                (
                    candidate
                    for candidate in latest.get("rooms") or []
                    if candidate.get("roomId") == roomId
                ),
                None,
            )
            if latest_room is None:
                return json.dumps(
                    {"success": False, "message": "外部系统未找到指定会议室"},
                    ensure_ascii=False,
                )
            if not latest_room.get("available", False):
                return json.dumps(
                    _conflict_result(
                        latest_room,
                        start=time.start,
                        end=time.end,
                    ),
                    ensure_ascii=False,
                )
            context = _booking_draft_context.get()
            result = client.create_draft(
                room_id=roomId,
                floor=resolved_floor,
                date=resolved_date,
                time_range=time.time_range,
                capacity=actual_capacity,
                theme=None,
                session_id=context[0] if context else None,
                round_no=context[1] if context else None,
            )
            result = {
                "success": True,
                **result,
                "message": "请在预约卡片中检查参数并由你本人确认",
            }
        except MeetingRoomConflictError:
            try:
                latest = _select_meet(
                    client,
                    floor=resolved_floor,
                    room_query=roomId,
                    date=resolved_date,
                    time_range=time.time_range,
                    capacity=actual_capacity,
                    available_only=False,
                )
                latest_room = next(
                    (
                        candidate
                        for candidate in latest.get("rooms") or []
                        if candidate.get("roomId") == roomId
                    ),
                    {},
                )
                result = _conflict_result(
                    latest_room,
                    start=time.start,
                    end=time.end,
                )
            except (MeetingRoomError, MockSandboxError):
                result = {
                    "success": False,
                    "conflict": True,
                    "occupiedBy": None,
                    "occupiedTimeRange": None,
                    "message": "该会议室在当前时段已被占用",
                }
        except (MeetingRoomError, MockSandboxError) as exc:
            result = {"success": False, "message": str(exc)}
        return json.dumps(result, ensure_ascii=False)

    return [search_meeting_rooms, book_meeting_room]


__all__ = [
    "DEFAULT_MEETING_CAPACITY",
    "DEFAULT_MEETING_DURATION_MINUTES",
    "MAX_PARALLEL_FLOOR_QUERIES",
    "BookMeetingRoomInput",
    "FloorInput",
    "MeetingRoomAgentClient",
    "MeetingRoomClient",
    "MeetingRoomConflictError",
    "MeetingRoomDateInput",
    "MeetingRoomDraftNotFoundError",
    "MeetingRoomDraftStateError",
    "MeetingRoomError",
    "MeetingRoomNotFoundError",
    "MeetingRoomRequirementsInput",
    "MeetingRoomTimeInput",
    "SearchMeetingRoomsInput",
    "SearchMeetingRoomsTimeInput",
    "create_meeting_room_tools",
    "reset_booking_draft_context",
    "set_booking_draft_context",
]
