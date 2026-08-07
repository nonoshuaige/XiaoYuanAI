"""Shared meeting-room domain rules with no HTTP or persistence dependencies."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
WORKDAY_START_MINUTES = 9 * 60
WORKDAY_END_MINUTES = 18 * 60 + 30
SLOT_MINUTES = 30
DEFAULT_MEETING_DURATION_MINUTES = 60
DEFAULT_MEETING_CAPACITY = 5
DRAFT_EXPIRY_MINUTES = 30


class MeetingRoomError(ValueError):
    """Base error for expected meeting-room failures."""


class MeetingRoomNotFoundError(MeetingRoomError):
    """The requested room does not exist in the external system."""


class MeetingRoomConflictError(MeetingRoomError):
    """The requested room overlaps an external booking."""


class MeetingRoomDraftNotFoundError(MeetingRoomError):
    """The requested local confirmation draft does not exist."""


class MeetingRoomDraftStateError(MeetingRoomError):
    """The requested draft can no longer be edited or confirmed."""


def validate_floor(value: str) -> str:
    normalized = value.strip()
    if not normalized.isdigit():
        raise MeetingRoomError("楼层必须是纯数字")
    return str(int(normalized))


def validate_date(value: str) -> str:
    normalized = value.strip()
    try:
        parsed = datetime.strptime(normalized, "%Y/%m/%d")
    except ValueError as exc:
        raise MeetingRoomError("日期格式必须为yyyy/MM/dd") from exc
    return parsed.strftime("%Y/%m/%d")


def parse_time_range(value: str) -> tuple[str, str]:
    parts = value.strip().split("-")
    if len(parts) != 2:
        raise MeetingRoomError("时间段格式必须为HH:mm-HH:mm")
    normalized = []
    for part in parts:
        try:
            parsed = datetime.strptime(part.strip(), "%H:%M")
        except ValueError as exc:
            raise MeetingRoomError("时间段格式必须为HH:mm-HH:mm") from exc
        normalized.append(parsed.strftime("%H:%M"))
    start_time, end_time = normalized
    if start_time >= end_time:
        raise MeetingRoomError("预约结束时间必须晚于开始时间")
    return start_time, end_time


def validate_bookable_slot(
    date: str,
    start_time: str,
    end_time: str,
    *,
    now: datetime,
) -> None:
    start_minutes = time_to_minutes(start_time)
    end_minutes = time_to_minutes(end_time)
    if (
        start_minutes < WORKDAY_START_MINUTES
        or end_minutes > WORKDAY_END_MINUTES
    ):
        raise MeetingRoomError("会议室仅支持工作时段09:00-18:30预约")
    if start_minutes % SLOT_MINUTES or end_minutes % SLOT_MINUTES:
        raise MeetingRoomError("预约时间必须按30分钟整点或半点选择")
    localized_now = (
        now.replace(tzinfo=SHANGHAI_TIMEZONE)
        if now.tzinfo is None
        else now.astimezone(SHANGHAI_TIMEZONE)
    )
    booking_start = datetime.strptime(
        f"{date} {start_time}", "%Y/%m/%d %H:%M"
    ).replace(tzinfo=SHANGHAI_TIMEZONE)
    booking_end = datetime.strptime(
        f"{date} {end_time}", "%Y/%m/%d %H:%M"
    ).replace(tzinfo=SHANGHAI_TIMEZONE)
    earliest_start_minutes = ceil_bookable_slot_start_minutes(localized_now)
    earliest_start = localized_now.replace(
        hour=earliest_start_minutes // 60,
        minute=earliest_start_minutes % 60,
        second=0,
        microsecond=0,
    )
    if booking_start < earliest_start or booking_end <= localized_now:
        raise MeetingRoomError("不能预约过去的时间")


def time_to_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def minutes_to_time(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def ceil_bookable_slot_start_minutes(now: datetime) -> int:
    """Return the current boundary or next half-hour boundary."""
    localized = (
        now.replace(tzinfo=SHANGHAI_TIMEZONE)
        if now.tzinfo is None
        else now.astimezone(SHANGHAI_TIMEZONE)
    )
    current = localized.hour * 60 + localized.minute
    remainder = current % SLOT_MINUTES
    rounded = current if remainder == 0 else current + SLOT_MINUTES - remainder
    return min(WORKDAY_END_MINUTES, max(WORKDAY_START_MINUTES, rounded))


def overlaps(
    first_start: str,
    first_end: str,
    second_start: str,
    second_end: str,
) -> bool:
    return first_start < second_end and first_end > second_start
