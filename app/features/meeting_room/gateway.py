"""External meeting-room gateway backed by the local Mock Sandbox API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from app.features.meeting_room.domain import (
    SHANGHAI_TIMEZONE,
    SLOT_MINUTES,
    WORKDAY_END_MINUTES,
    WORKDAY_START_MINUTES,
    MeetingRoomError,
    minutes_to_time,
    overlaps,
    parse_time_range,
    time_to_minutes,
    validate_date,
    validate_floor,
)
from app.integrations.mock_sandbox.client import (
    MockSandboxError,
    MockSandboxHttpClient,
    MockSandboxSettings,
)


class MeetingRoomGateway(Protocol):
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

    def create(
        self,
        *,
        room_id: str,
        floor: str,
        date: str,
        time_range: str,
        capacity: int,
        theme: str | None,
    ) -> dict[str, Any]: ...


class MockSandboxMeetingRoomGateway:
    """Translate Mock Sandbox search/book responses into Agent contracts."""

    def __init__(self, http: MockSandboxHttpClient, *, now_factory=None):
        self.http = http
        self._now_factory = now_factory or (
            lambda: datetime.now(SHANGHAI_TIMEZONE)
        )
        self._token: str | None = None
        self._token_user_id: str | None = None

    def select_meet(
        self,
        *,
        floor: str | None = None,
        room_query: str | None = None,
        date: str | None = None,
        time_range: str | None = None,
        capacity: int | None = None,
        available_only: bool = False,
    ) -> dict[str, Any]:
        normalized_floor = validate_floor(floor) if floor is not None else None
        normalized_room_query = (room_query or "").strip() or None
        requested_date = (
            validate_date(date)
            if date is not None
            else self._now_factory().strftime("%Y/%m/%d")
        )
        requested_slot = (
            parse_time_range(time_range) if time_range is not None else None
        )
        if capacity is not None and capacity < 1:
            raise MeetingRoomError("参会人数必须大于0")
        payload = self.http.request_json(
            "POST",
            "/oca/ibpmeetrese/q/selectMeet",
            json_body={"address": normalized_floor or ""},
            headers={"Userauthorization": self._login()},
        )
        if int(payload.get("code", -1)) != 0:
            raise MockSandboxError(
                str(payload.get("message") or "外部会议室查询失败")
            )
        raw_rooms = payload.get("data")
        if not isinstance(raw_rooms, list):
            raise MockSandboxError("外部会议室响应格式错误")

        now = self._now_factory()
        is_today = requested_date == now.strftime("%Y/%m/%d")
        current_time = now.strftime("%H:%M")
        display_start_minutes = (
            _current_slot_start_minutes(now)
            if is_today
            else WORKDAY_START_MINUTES
        )
        display_start_time = minutes_to_time(display_start_minutes)
        result_rooms = []
        for raw_room in raw_rooms:
            if not isinstance(raw_room, dict):
                continue
            room_id = str(raw_room.get("roomId") or "")
            room_name = str(raw_room.get("roomName") or room_id)
            room_floor = str(raw_room.get("address") or "")
            if not room_id or not room_floor:
                continue
            if normalized_room_query and (
                normalized_room_query not in room_id
                and normalized_room_query not in room_name
            ):
                continue
            room_capacity = _room_capacity(raw_room.get("capacity"))
            room_bookings = [
                _booking(room_id, item)
                for item in (raw_room.get("reservedInfoList") or [])
                if isinstance(item, dict)
                and str(item.get("reservedDate") or "").replace("-", "/")
                == requested_date
            ]
            visible_bookings = [
                booking
                for booking in room_bookings
                if booking["end_time"] > display_start_time
            ]
            slot_available = requested_slot is None or not any(
                overlaps(
                    requested_slot[0],
                    requested_slot[1],
                    booking["start_time"],
                    booking["end_time"],
                )
                for booking in room_bookings
            )
            capacity_available = capacity is None or room_capacity >= capacity
            available = slot_available and capacity_available
            if available_only and not available:
                continue
            timeline = _timeline(
                visible_bookings,
                capacity_available=capacity_available,
                start_minutes=display_start_minutes,
            )
            available_ranges = _available_ranges(timeline)
            suggested_ranges = (
                _matching_ranges(
                    timeline,
                    duration_minutes=(
                        time_to_minutes(requested_slot[1])
                        - time_to_minutes(requested_slot[0])
                    ),
                )
                if requested_slot is not None
                else available_ranges
            )
            current_booking = next(
                (
                    booking
                    for booking in room_bookings
                    if is_today
                    and booking["start_time"] <= current_time < booking["end_time"]
                ),
                None,
            )
            equipment = [
                item.strip()
                for item in str(raw_room.get("equip") or "").split(";")
                if item.strip()
            ]
            result_rooms.append(
                {
                    "roomId": room_id,
                    "roomName": room_name,
                    "floor": f"{room_floor}F",
                    "capacity": room_capacity,
                    "equipment": equipment,
                    "available": available,
                    "currentStatus": (
                        "occupied" if current_booking else "available"
                    ),
                    "currentBooking": (
                        _booking_dict(current_booking)
                        if current_booking
                        else None
                    ),
                    "occupied": [
                        _booking_dict(item) for item in visible_bookings
                    ],
                    "timeline": timeline,
                    "availableTimeRanges": available_ranges,
                    "suggestedTimeRanges": suggested_ranges,
                }
            )
        return {
            "success": True,
            "source": "mock-sandbox",
            "observedAt": now.isoformat(timespec="seconds"),
            "date": requested_date,
            "timeRange": time_range,
            "capacity": capacity,
            "floor": f"{normalized_floor}F" if normalized_floor else None,
            "roomQuery": normalized_room_query,
            "scheduleWindow": "09:00-18:00",
            "displayWindow": (
                f"{display_start_time}-{minutes_to_time(WORKDAY_END_MINUTES)}"
            ),
            "slotMinutes": SLOT_MINUTES,
            "rooms": result_rooms,
        }

    def create(
        self,
        *,
        room_id: str,
        floor: str,
        date: str,
        time_range: str,
        capacity: int,
        theme: str | None,
    ) -> dict[str, Any]:
        start_time, end_time = parse_time_range(time_range)
        settings = self.http.settings
        resolved_theme = (theme or "").strip() or (
            f"{settings.user_name}预约的会议"
        )
        payload = self.http.request_json(
            "POST",
            "/oca/ibpmeetrese/n/create",
            json_body={
                "reservedDate": validate_date(date).replace("/", "-"),
                "meetingType": "1",
                "roomId": room_id,
                "capacity": f"{capacity};{capacity}",
                "startTime": start_time,
                "endTime": end_time,
                "theme": resolved_theme,
                "reserveUserName": settings.user_name,
                "reserveUserId": settings.user_id,
                "createUser": settings.user_id,
                "address": validate_floor(floor),
            },
            headers={"Userauthorization": self._login(settings)},
        )
        data = payload.get("data")
        if int(payload.get("code", -1)) != 0 or not isinstance(data, dict):
            raise MockSandboxError(
                str(payload.get("message") or "外部会议室预约失败")
            )
        return {
            "success": True,
            "bookingId": str(data.get("reserveId") or data.get("id") or ""),
            "meetingId": str(data.get("meetingId") or ""),
            "roomId": str(data.get("roomId") or room_id),
            "roomName": str(data.get("roomName") or room_id),
            "date": str(data.get("reservedDate") or date).replace("-", "/"),
            "timeRange": (
                f"{data.get('startTime') or start_time}-"
                f"{data.get('endTime') or end_time}"
            ),
            "capacity": capacity,
            "theme": str(data.get("theme") or resolved_theme),
            "message": "会议室预约成功",
            "source": "mock-sandbox",
        }

    def _login(self, settings: MockSandboxSettings | None = None) -> str:
        settings = settings or self.http.settings
        if self._token and self._token_user_id == settings.user_id:
            return self._token
        payload = self.http.request_json(
            "POST",
            "/oca/applet/login",
            json_body={
                "loginCode": settings.user_id,
                "userId": settings.user_id,
                "userName": settings.user_name,
                "clientId": "XiaoYuanAI",
            },
        )
        data = payload.get("data")
        token = data.get("tokenId") if isinstance(data, dict) else None
        if int(payload.get("code", -1)) != 0 or not token:
            raise MockSandboxError("获取外部会议室 Token 失败")
        self._token = str(token)
        self._token_user_id = settings.user_id
        return self._token


def _room_capacity(value: Any) -> int:
    candidates: list[int] = []
    for item in str(value or "").split(";"):
        try:
            candidates.append(int(item.strip()))
        except ValueError:
            continue
    return max(candidates, default=0)


def _booking(room_id: str, item: dict[str, Any]) -> dict[str, Any]:
    booking_date = str(item.get("reservedDate") or "").replace("-", "/")
    start_time = str(item.get("startTime") or "")
    end_time = str(item.get("endTime") or "")
    identity = "-".join(
        part.replace("/", "").replace(":", "")
        for part in (room_id, booking_date, start_time, end_time)
    )
    return {
        "booking_id": f"mock-{identity}",
        "meeting_id": "",
        "start_time": start_time,
        "end_time": end_time,
        "capacity": 0,
        "theme": str(item.get("theme") or "已预约"),
        "booked_by": str(item.get("userName") or item.get("userNo") or ""),
        "source": "mock-sandbox",
        "created_at": "",
    }


def _booking_dict(booking: dict[str, Any]) -> dict[str, Any]:
    return {
        "bookingId": booking["booking_id"],
        "meetingId": booking["meeting_id"],
        "timeRange": f"{booking['start_time']}-{booking['end_time']}",
        "capacity": booking["capacity"],
        "theme": booking["theme"],
        "bookedBy": booking["booked_by"],
        "source": booking["source"],
        "createdAt": booking["created_at"],
    }


def _timeline(
    bookings: list[dict[str, Any]],
    *,
    capacity_available: bool,
    start_minutes: int,
) -> list[dict[str, Any]]:
    result = []
    for slot_start in range(start_minutes, WORKDAY_END_MINUTES, SLOT_MINUTES):
        start = minutes_to_time(slot_start)
        end = minutes_to_time(slot_start + SLOT_MINUTES)
        booking = next(
            (
                item
                for item in bookings
                if overlaps(start, end, item["start_time"], item["end_time"])
            ),
            None,
        )
        available = capacity_available and booking is None
        result.append(
            {
                "start": start,
                "end": end,
                "timeRange": f"{start}-{end}",
                "available": available,
                "status": (
                    "available"
                    if available
                    else ("occupied" if booking else "capacity")
                ),
                "booking": _booking_dict(booking) if booking else None,
            }
        )
    return result


def _current_slot_start_minutes(now: datetime) -> int:
    localized = (
        now.replace(tzinfo=SHANGHAI_TIMEZONE)
        if now.tzinfo is None
        else now.astimezone(SHANGHAI_TIMEZONE)
    )
    current = localized.hour * 60 + localized.minute
    rounded = current - current % SLOT_MINUTES
    return min(WORKDAY_END_MINUTES, max(WORKDAY_START_MINUTES, rounded))


def _available_ranges(timeline: list[dict[str, Any]]) -> list[str]:
    ranges: list[str] = []
    range_start: str | None = None
    for slot in timeline:
        if slot["available"] and range_start is None:
            range_start = slot["start"]
        if not slot["available"] and range_start is not None:
            ranges.append(f"{range_start}-{slot['start']}")
            range_start = None
    if range_start is not None:
        ranges.append(f"{range_start}-{minutes_to_time(WORKDAY_END_MINUTES)}")
    return ranges


def _matching_ranges(
    timeline: list[dict[str, Any]],
    *,
    duration_minutes: int,
) -> list[str]:
    slots_needed = max(1, (duration_minutes + SLOT_MINUTES - 1) // SLOT_MINUTES)
    matches = []
    for index in range(0, len(timeline) - slots_needed + 1):
        window = timeline[index : index + slots_needed]
        if all(slot["available"] for slot in window):
            matches.append(f"{window[0]['start']}-{window[-1]['end']}")
    return matches
