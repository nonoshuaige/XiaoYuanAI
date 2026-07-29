"""Meeting-room persistence and sandbox-backed agent tools."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextvars import ContextVar, Token
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, model_validator

from conversation_store import DEFAULT_DB_PATH


SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
WORKDAY_START_MINUTES = 9 * 60
WORKDAY_END_MINUTES = 18 * 60
SLOT_MINUTES = 30
DEFAULT_MEETING_CAPACITY = 5
DRAFT_EXPIRY_MINUTES = 30
_booking_draft_context: ContextVar[tuple[str, int] | None] = ContextVar(
    "meeting_room_booking_draft_context",
    default=None,
)

SANDBOX_MEETING_ROOMS = (
    {
        "room_id": "room-601",
        "room_name": "静思 601",
        "floor": "6",
        "capacity": 4,
        "equipment": ["显示屏"],
    },
    {
        "room_id": "room-603",
        "room_name": "协作 603",
        "floor": "6",
        "capacity": 10,
        "equipment": ["投影仪", "白板"],
    },
    {
        "room_id": "room-606",
        "room_name": "灵感 606",
        "floor": "6",
        "capacity": 16,
        "equipment": ["视频会议", "白板"],
    },
    {
        "room_id": "room-707",
        "room_name": "707会议室",
        "floor": "7",
        "capacity": 20,
        "equipment": ["投影仪", "白板", "视频会议"],
    },
    {
        "room_id": "room-708",
        "room_name": "云杉 708",
        "floor": "7",
        "capacity": 8,
        "equipment": ["显示屏", "白板"],
    },
    {
        "room_id": "room-711",
        "room_name": "远望 711",
        "floor": "7",
        "capacity": 12,
        "equipment": ["投影仪", "视频会议"],
    },
    {
        "room_id": "room-801",
        "room_name": "天际 801",
        "floor": "8",
        "capacity": 30,
        "equipment": ["LED大屏", "视频会议", "会议电话"],
    },
    {
        "room_id": "room-802",
        "room_name": "星河 802",
        "floor": "8",
        "capacity": 10,
        "equipment": ["投影仪", "白板"],
    },
    {
        "room_id": "room-806",
        "room_name": "远景 806",
        "floor": "8",
        "capacity": 12,
        "equipment": ["显示屏", "视频会议"],
    },
)

SAMPLE_BOOKINGS = (
    ("room-603", "10:30", "11:30", "设计评审"),
    ("room-707", "09:00", "10:00", "产品周会"),
    ("room-708", "14:00", "15:30", "需求讨论"),
    ("room-802", "13:00", "14:00", "客户沟通"),
)


class MeetingRoomError(ValueError):
    """Base error for expected meeting-room failures."""


class MeetingRoomNotFoundError(MeetingRoomError):
    """The requested room does not exist on the requested floor."""


class MeetingRoomConflictError(MeetingRoomError):
    """The requested room overlaps an existing booking."""


class MeetingRoomDraftNotFoundError(MeetingRoomError):
    """The requested pending booking draft does not exist."""


class MeetingRoomDraftStateError(MeetingRoomError):
    """The requested draft can no longer be edited or confirmed."""


def set_booking_draft_context(session_id: str, round_no: int) -> Token:
    """Attach durable conversation ownership without exposing it to the LLM."""
    return _booking_draft_context.set((session_id, round_no))


def reset_booking_draft_context(token: Token) -> None:
    _booking_draft_context.reset(token)


class MeetingRoomStore:
    """SQLite repository shared by the sandbox UI and agent tools."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        *,
        now_factory=None,
        seed_sandbox_data: bool = True,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self._now_factory = now_factory or (
            lambda: datetime.now(SHANGHAI_TIMEZONE)
        )
        self._init_schema()
        if seed_sandbox_data:
            self.seed_sandbox_data()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meeting_rooms (
                    room_id TEXT PRIMARY KEY,
                    room_name TEXT NOT NULL,
                    floor TEXT NOT NULL,
                    capacity INTEGER NOT NULL CHECK (capacity > 0),
                    equipment_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_meeting_rooms_floor
                ON meeting_rooms(floor, room_name);

                CREATE TABLE IF NOT EXISTS meeting_room_bookings (
                    booking_id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL UNIQUE,
                    room_id TEXT NOT NULL,
                    booking_date TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    capacity INTEGER NOT NULL CHECK (capacity > 0),
                    theme TEXT NOT NULL,
                    booked_by TEXT NOT NULL,
                    source TEXT NOT NULL
                        CHECK (source IN ('sample', 'interactive')),
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (room_id) REFERENCES meeting_rooms(room_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_meeting_room_bookings_slot
                ON meeting_room_bookings(
                    booking_date,
                    room_id,
                    start_time,
                    end_time
                );

                CREATE TABLE IF NOT EXISTS meeting_room_booking_drafts (
                    draft_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    round_no INTEGER,
                    room_id TEXT NOT NULL,
                    floor TEXT NOT NULL,
                    booking_date TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    capacity INTEGER NOT NULL CHECK (capacity > 0),
                    theme TEXT NOT NULL,
                    booked_by TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK (
                            status IN (
                                'pending',
                                'confirmed',
                                'cancelled',
                                'expired'
                            )
                        ),
                    booking_id TEXT,
                    meeting_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (room_id) REFERENCES meeting_rooms(room_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_meeting_room_drafts_session
                ON meeting_room_booking_drafts(session_id, round_no, created_at);
                """
            )

    def seed_sandbox_data(self) -> None:
        """Idempotently create the room inventory and today's sample schedule."""
        today = self._now_factory().strftime("%Y/%m/%d")
        created_at = self._now_factory().isoformat(timespec="seconds")
        with self._write_lock, self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO meeting_rooms(
                    room_id,
                    room_name,
                    floor,
                    capacity,
                    equipment_json
                )
                VALUES(
                    :room_id,
                    :room_name,
                    :floor,
                    :capacity,
                    :equipment_json
                )
                ON CONFLICT(room_id) DO UPDATE SET
                    room_name = excluded.room_name,
                    floor = excluded.floor,
                    capacity = excluded.capacity,
                    equipment_json = excluded.equipment_json
                """,
                [
                    {
                        **room,
                        "equipment_json": json.dumps(
                            room["equipment"],
                            ensure_ascii=False,
                        ),
                    }
                    for room in SANDBOX_MEETING_ROOMS
                ],
            )
            connection.executemany(
                """
                INSERT INTO meeting_room_bookings(
                    booking_id,
                    meeting_id,
                    room_id,
                    booking_date,
                    start_time,
                    end_time,
                    capacity,
                    theme,
                    booked_by,
                    source,
                    created_at
                )
                VALUES(
                    :booking_id,
                    :meeting_id,
                    :room_id,
                    :booking_date,
                    :start_time,
                    :end_time,
                    5,
                    :theme,
                    '沙箱示例',
                    'sample',
                    :created_at
                )
                ON CONFLICT(booking_id) DO NOTHING
                """,
                [
                    {
                        "booking_id": (
                            f"sample-{today.replace('/', '')}-"
                            f"{room_id}-{start.replace(':', '')}"
                        ),
                        "meeting_id": (
                            f"SAMPLE-{today.replace('/', '')}-"
                            f"{room_id.removeprefix('room-')}-"
                            f"{start.replace(':', '')}"
                        ),
                        "room_id": room_id,
                        "booking_date": today,
                        "start_time": start,
                        "end_time": end,
                        "theme": theme,
                        "created_at": created_at,
                    }
                    for room_id, start, end, theme in SAMPLE_BOOKINGS
                ],
            )

    def list_rooms(
        self,
        *,
        floor: str | None = None,
        room_query: str | None = None,
        date: str | None = None,
        time_range: str | None = None,
        capacity: int | None = None,
        available_only: bool = False,
    ) -> dict[str, Any]:
        normalized_floor = _validate_floor(floor) if floor is not None else None
        normalized_room_query = (
            room_query.strip() if room_query is not None else None
        )
        if normalized_room_query == "":
            normalized_room_query = None
        requested_date = (
            _validate_date(date)
            if date is not None
            else self._now_factory().strftime("%Y/%m/%d")
        )
        requested_slot = (
            _parse_time_range(time_range) if time_range is not None else None
        )
        if capacity is not None and capacity < 1:
            raise MeetingRoomError("参会人数必须大于0")

        with self._connect() as connection:
            conditions: list[str] = []
            parameters: list[Any] = []
            if normalized_floor is not None:
                conditions.append("floor = ?")
                parameters.append(normalized_floor)
            if normalized_room_query is not None:
                conditions.append(
                    "(room_id LIKE ? OR room_name LIKE ?)"
                )
                contains_room = f"%{normalized_room_query}%"
                parameters.extend((contains_room, contains_room))
            where_clause = (
                f"WHERE {' AND '.join(conditions)}" if conditions else ""
            )
            rooms = connection.execute(
                f"""
                SELECT room_id, room_name, floor, capacity, equipment_json
                FROM meeting_rooms
                {where_clause}
                ORDER BY CAST(floor AS INTEGER), room_name
                """,
                parameters,
            ).fetchall()
            room_ids = [room["room_id"] for room in rooms]
            if room_ids:
                placeholders = ", ".join("?" for _ in room_ids)
                bookings = connection.execute(
                    f"""
                    SELECT
                        booking_id,
                        meeting_id,
                        room_id,
                        start_time,
                        end_time,
                        capacity,
                        theme,
                        booked_by,
                        source,
                        created_at
                    FROM meeting_room_bookings
                    WHERE booking_date = ?
                        AND room_id IN ({placeholders})
                    ORDER BY start_time, room_id
                    """,
                    (requested_date, *room_ids),
                ).fetchall()
            else:
                bookings = []

        bookings_by_room: dict[str, list[sqlite3.Row]] = {}
        for booking in bookings:
            bookings_by_room.setdefault(booking["room_id"], []).append(booking)

        now = self._now_factory()
        current_time = now.strftime("%H:%M")
        is_today = requested_date == now.strftime("%Y/%m/%d")
        display_start_minutes = (
            _current_slot_start_minutes(now)
            if is_today
            else WORKDAY_START_MINUTES
        )
        display_start_time = _minutes_to_time(display_start_minutes)
        result_rooms = []
        for room in rooms:
            room_bookings = bookings_by_room.get(room["room_id"], [])
            visible_bookings = [
                booking
                for booking in room_bookings
                if booking["end_time"] > display_start_time
            ]
            slot_available = (
                requested_slot is None
                or not any(
                    _overlaps(
                        requested_slot[0],
                        requested_slot[1],
                        booking["start_time"],
                        booking["end_time"],
                    )
                    for booking in room_bookings
                )
            )
            capacity_available = (
                capacity is None or room["capacity"] >= capacity
            )
            available = slot_available and capacity_available
            if available_only and not available:
                continue
            timeline = _build_half_hour_timeline(
                visible_bookings,
                capacity_available=capacity_available,
                start_minutes=display_start_minutes,
            )
            available_time_ranges = _merge_available_slots(timeline)
            suggested_time_ranges = (
                _matching_duration_ranges(
                    timeline,
                    duration_minutes=_range_duration_minutes(
                        requested_slot
                    ),
                )
                if requested_slot is not None
                else available_time_ranges
            )
            current_booking = next(
                (
                    booking
                    for booking in room_bookings
                    if is_today
                    and booking["start_time"] <= current_time
                    < booking["end_time"]
                ),
                None,
            )
            result_rooms.append(
                {
                    "roomId": room["room_id"],
                    "roomName": room["room_name"],
                    "floor": f"{room['floor']}F",
                    "capacity": room["capacity"],
                    "equipment": json.loads(room["equipment_json"]),
                    "available": available,
                    "currentStatus": (
                        "occupied" if current_booking else "available"
                    ),
                    "currentBooking": (
                        _booking_dict(current_booking)
                        if current_booking is not None
                        else None
                    ),
                    "occupied": [
                        _booking_dict(booking)
                        for booking in visible_bookings
                    ],
                    "timeline": timeline,
                    "availableTimeRanges": available_time_ranges,
                    "suggestedTimeRanges": suggested_time_ranges,
                }
            )

        return {
            "success": True,
            "sandbox": True,
            "observedAt": now.isoformat(timespec="seconds"),
            "date": requested_date,
            "timeRange": time_range,
            "capacity": capacity,
            "floor": (
                f"{normalized_floor}F"
                if normalized_floor is not None
                else None
            ),
            "roomQuery": normalized_room_query,
            "scheduleWindow": "09:00-18:00",
            "displayWindow": (
                f"{display_start_time}-"
                f"{_minutes_to_time(WORKDAY_END_MINUTES)}"
            ),
            "slotMinutes": SLOT_MINUTES,
            "rooms": result_rooms,
        }

    def create_booking(
        self,
        *,
        room_id: str,
        floor: str,
        date: str,
        time_range: str,
        capacity: int = DEFAULT_MEETING_CAPACITY,
        theme: str | None = None,
        booked_by: str = "沙箱访客",
    ) -> dict[str, Any]:
        room_id = room_id.strip()
        floor = _validate_floor(floor)
        date = _validate_date(date)
        start_time, end_time = _parse_time_range(time_range)
        _validate_bookable_slot(
            date,
            start_time,
            end_time,
            now=self._now_factory(),
        )
        if not room_id:
            raise MeetingRoomError("会议室ID不能为空")
        if capacity < 1:
            raise MeetingRoomError("参会人数必须大于0")
        normalized_booked_by = booked_by.strip() or "沙箱访客"
        default_theme = f"{normalized_booked_by}预约的会议"
        normalized_theme = (theme or "").strip() or default_theme
        created_at = self._now_factory().isoformat(timespec="seconds")
        booking_id = uuid.uuid4().hex
        meeting_id = (
            f"NMS{date.replace('/', '')}"
            f"{uuid.uuid4().int % 10**13:013d}"
        )

        with self._write_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            room = connection.execute(
                """
                SELECT room_id, room_name, floor, capacity
                FROM meeting_rooms
                WHERE room_id = ? AND floor = ?
                """,
                (room_id, floor),
            ).fetchone()
            if room is None:
                raise MeetingRoomNotFoundError("没有找到该楼层的指定会议室")
            if capacity > room["capacity"]:
                raise MeetingRoomError(
                    f"会议室最多容纳{room['capacity']}人"
                )
            conflict = connection.execute(
                """
                SELECT booking_id
                FROM meeting_room_bookings
                WHERE room_id = ?
                    AND booking_date = ?
                    AND start_time < ?
                    AND end_time > ?
                LIMIT 1
                """,
                (room_id, date, end_time, start_time),
            ).fetchone()
            if conflict is not None:
                raise MeetingRoomConflictError(
                    "该会议室在当前时段已被预订"
                )
            connection.execute(
                """
                INSERT INTO meeting_room_bookings(
                    booking_id,
                    meeting_id,
                    room_id,
                    booking_date,
                    start_time,
                    end_time,
                    capacity,
                    theme,
                    booked_by,
                    source,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'interactive', ?)
                """,
                (
                    booking_id,
                    meeting_id,
                    room_id,
                    date,
                    start_time,
                    end_time,
                    capacity,
                    normalized_theme,
                    normalized_booked_by,
                    created_at,
                ),
            )

        return {
            "success": True,
            "bookingId": booking_id,
            "meetingId": meeting_id,
            "roomId": room_id,
            "roomName": room["room_name"],
            "date": date,
            "timeRange": f"{start_time}-{end_time}",
            "capacity": capacity,
            "theme": normalized_theme,
            "message": "会议室预约成功",
        }

    def create_draft(
        self,
        *,
        room_id: str,
        floor: str,
        date: str,
        time_range: str,
        capacity: int = DEFAULT_MEETING_CAPACITY,
        theme: str | None = None,
        booked_by: str = "沙箱访客",
        session_id: str | None = None,
        round_no: int | None = None,
    ) -> dict[str, Any]:
        """Create an editable proposal; this method never creates a booking."""
        room_id = room_id.strip()
        floor = _validate_floor(floor)
        date = _validate_date(date)
        start_time, end_time = _parse_time_range(time_range)
        now = self._now_factory()
        _validate_bookable_slot(date, start_time, end_time, now=now)
        if not room_id:
            raise MeetingRoomError("会议室ID不能为空")
        if capacity < 1:
            raise MeetingRoomError("参会人数必须大于0")
        normalized_booked_by = booked_by.strip() or "沙箱访客"
        normalized_theme = (
            (theme or "").strip()
            or f"{normalized_booked_by}预约的会议"
        )
        draft_id = uuid.uuid4().hex
        created_at = now.isoformat(timespec="seconds")
        expires_at = (
            now + timedelta(minutes=DRAFT_EXPIRY_MINUTES)
        ).isoformat(timespec="seconds")

        with self._write_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._validate_room_and_conflict(
                connection,
                room_id=room_id,
                floor=floor,
                date=date,
                start_time=start_time,
                end_time=end_time,
                capacity=capacity,
            )
            connection.execute(
                """
                INSERT INTO meeting_room_booking_drafts(
                    draft_id,
                    session_id,
                    round_no,
                    room_id,
                    floor,
                    booking_date,
                    start_time,
                    end_time,
                    capacity,
                    theme,
                    booked_by,
                    status,
                    booking_id,
                    meeting_id,
                    created_at,
                    updated_at,
                    expires_at
                )
                VALUES(
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'pending', NULL, NULL, ?, ?, ?
                )
                """,
                (
                    draft_id,
                    session_id,
                    round_no,
                    room_id,
                    floor,
                    date,
                    start_time,
                    end_time,
                    capacity,
                    normalized_theme,
                    normalized_booked_by,
                    created_at,
                    created_at,
                    expires_at,
                ),
            )
        return self.get_draft(draft_id)

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        with self._write_lock, self._connect() as connection:
            row = self._load_draft(connection, draft_id)
            row = self._expire_draft_if_needed(connection, row)
            return self._draft_dict(connection, row)

    def list_drafts(
        self,
        *,
        session_id: str,
    ) -> list[dict[str, Any]]:
        with self._write_lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM meeting_room_booking_drafts
                WHERE session_id = ?
                ORDER BY round_no, created_at
                """,
                (session_id,),
            ).fetchall()
            return [
                self._draft_dict(
                    connection,
                    self._expire_draft_if_needed(connection, row),
                )
                for row in rows
            ]

    def update_draft(
        self,
        draft_id: str,
        *,
        room_id: str,
        floor: str,
        date: str,
        time_range: str,
        capacity: int,
        theme: str | None,
    ) -> dict[str, Any]:
        room_id = room_id.strip()
        floor = _validate_floor(floor)
        date = _validate_date(date)
        start_time, end_time = _parse_time_range(time_range)
        now = self._now_factory()
        _validate_bookable_slot(date, start_time, end_time, now=now)
        if capacity < 1:
            raise MeetingRoomError("参会人数必须大于0")
        with self._write_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            draft = self._expire_draft_if_needed(
                connection,
                self._load_draft(connection, draft_id),
            )
            if draft["status"] != "pending":
                raise MeetingRoomDraftStateError("该预约卡片已失效，无法修改")
            room = self._validate_room_and_conflict(
                connection,
                room_id=room_id,
                floor=floor,
                date=date,
                start_time=start_time,
                end_time=end_time,
                capacity=capacity,
            )
            normalized_theme = (
                (theme or "").strip()
                or f"{draft['booked_by']}预约的会议"
            )
            updated_at = now.isoformat(timespec="seconds")
            connection.execute(
                """
                UPDATE meeting_room_booking_drafts
                SET
                    room_id = ?,
                    floor = ?,
                    booking_date = ?,
                    start_time = ?,
                    end_time = ?,
                    capacity = ?,
                    theme = ?,
                    updated_at = ?
                WHERE draft_id = ?
                """,
                (
                    room["room_id"],
                    floor,
                    date,
                    start_time,
                    end_time,
                    capacity,
                    normalized_theme,
                    updated_at,
                    draft_id,
                ),
            )
        return self.get_draft(draft_id)

    def confirm_draft(self, draft_id: str) -> dict[str, Any]:
        """The sole human-triggered final write path, with a fresh conflict check."""
        with self._write_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            draft = self._expire_draft_if_needed(
                connection,
                self._load_draft(connection, draft_id),
            )
            if draft["status"] == "confirmed":
                return self._confirmed_draft_result(connection, draft)
            if draft["status"] != "pending":
                raise MeetingRoomDraftStateError(
                    (
                        "该预约卡片已取消，无法确认"
                        if draft["status"] == "cancelled"
                        else "该预约卡片已失效，无法确认"
                    )
                )
            now = self._now_factory()
            _validate_bookable_slot(
                draft["booking_date"],
                draft["start_time"],
                draft["end_time"],
                now=now,
            )
            room = self._validate_room_and_conflict(
                connection,
                room_id=draft["room_id"],
                floor=draft["floor"],
                date=draft["booking_date"],
                start_time=draft["start_time"],
                end_time=draft["end_time"],
                capacity=draft["capacity"],
            )
            booking_id = uuid.uuid4().hex
            meeting_id = (
                f"NMS{draft['booking_date'].replace('/', '')}"
                f"{uuid.uuid4().int % 10**13:013d}"
            )
            created_at = now.isoformat(timespec="seconds")
            connection.execute(
                """
                INSERT INTO meeting_room_bookings(
                    booking_id,
                    meeting_id,
                    room_id,
                    booking_date,
                    start_time,
                    end_time,
                    capacity,
                    theme,
                    booked_by,
                    source,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'interactive', ?)
                """,
                (
                    booking_id,
                    meeting_id,
                    draft["room_id"],
                    draft["booking_date"],
                    draft["start_time"],
                    draft["end_time"],
                    draft["capacity"],
                    draft["theme"],
                    draft["booked_by"],
                    created_at,
                ),
            )
            connection.execute(
                """
                UPDATE meeting_room_booking_drafts
                SET
                    status = 'confirmed',
                    booking_id = ?,
                    meeting_id = ?,
                    updated_at = ?
                WHERE draft_id = ?
                """,
                (booking_id, meeting_id, created_at, draft_id),
            )
            confirmed = connection.execute(
                "SELECT * FROM meeting_room_booking_drafts WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
            return {
                "success": True,
                "bookingId": booking_id,
                "meetingId": meeting_id,
                "roomId": draft["room_id"],
                "roomName": room["room_name"],
                "date": draft["booking_date"],
                "timeRange": (
                    f"{draft['start_time']}-{draft['end_time']}"
                ),
                "capacity": draft["capacity"],
                "theme": draft["theme"],
                "message": "会议室预约成功",
                "draft": self._draft_dict(connection, confirmed),
            }

    def cancel_draft(self, draft_id: str) -> dict[str, Any]:
        """Cancel an unconfirmed draft without touching any real booking."""
        with self._write_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            draft = self._expire_draft_if_needed(
                connection,
                self._load_draft(connection, draft_id),
            )
            if draft["status"] == "cancelled":
                return self._draft_dict(connection, draft)
            if draft["status"] != "pending":
                raise MeetingRoomDraftStateError(
                    "只有待确认的预约卡片可以取消"
                )
            connection.execute(
                """
                UPDATE meeting_room_booking_drafts
                SET status = 'cancelled', updated_at = ?
                WHERE draft_id = ?
                """,
                (
                    self._now_factory().isoformat(timespec="seconds"),
                    draft_id,
                ),
            )
            return self._draft_dict(
                connection,
                self._load_draft(connection, draft_id),
            )

    def _load_draft(
        self,
        connection: sqlite3.Connection,
        draft_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM meeting_room_booking_drafts WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
        if row is None:
            raise MeetingRoomDraftNotFoundError("没有找到该预约卡片")
        return row

    def _expire_draft_if_needed(
        self,
        connection: sqlite3.Connection,
        draft: sqlite3.Row,
    ) -> sqlite3.Row:
        expires_at = datetime.fromisoformat(draft["expires_at"])
        if draft["status"] == "pending" and self._now_factory() >= expires_at:
            connection.execute(
                """
                UPDATE meeting_room_booking_drafts
                SET status = 'expired', updated_at = ?
                WHERE draft_id = ?
                """,
                (
                    self._now_factory().isoformat(timespec="seconds"),
                    draft["draft_id"],
                ),
            )
            return self._load_draft(connection, draft["draft_id"])
        return draft

    def _validate_room_and_conflict(
        self,
        connection: sqlite3.Connection,
        *,
        room_id: str,
        floor: str,
        date: str,
        start_time: str,
        end_time: str,
        capacity: int,
    ) -> sqlite3.Row:
        room = connection.execute(
            """
            SELECT room_id, room_name, floor, capacity
            FROM meeting_rooms
            WHERE room_id = ? AND floor = ?
            """,
            (room_id, floor),
        ).fetchone()
        if room is None:
            raise MeetingRoomNotFoundError("没有找到该楼层的指定会议室")
        if capacity > room["capacity"]:
            raise MeetingRoomError(f"会议室最多容纳{room['capacity']}人")
        conflict = connection.execute(
            """
            SELECT booking_id
            FROM meeting_room_bookings
            WHERE room_id = ?
                AND booking_date = ?
                AND start_time < ?
                AND end_time > ?
            LIMIT 1
            """,
            (room_id, date, end_time, start_time),
        ).fetchone()
        if conflict is not None:
            raise MeetingRoomConflictError("该会议室在当前时段已被预订")
        return room

    def _draft_dict(
        self,
        connection: sqlite3.Connection,
        draft: sqlite3.Row,
    ) -> dict[str, Any]:
        room = connection.execute(
            "SELECT room_name FROM meeting_rooms WHERE room_id = ?",
            (draft["room_id"],),
        ).fetchone()
        return {
            "type": "meetingRoomBookingDraft",
            "draftId": draft["draft_id"],
            "sessionId": draft["session_id"],
            "round": draft["round_no"],
            "roomId": draft["room_id"],
            "roomName": room["room_name"] if room else draft["room_id"],
            "floor": draft["floor"],
            "date": draft["booking_date"],
            "timeRange": f"{draft['start_time']}-{draft['end_time']}",
            "capacity": draft["capacity"],
            "theme": draft["theme"],
            "bookedBy": draft["booked_by"],
            "status": draft["status"],
            "bookingId": draft["booking_id"],
            "meetingId": draft["meeting_id"],
            "expiresAt": draft["expires_at"],
            "createdAt": draft["created_at"],
            "updatedAt": draft["updated_at"],
        }

    def _confirmed_draft_result(
        self,
        connection: sqlite3.Connection,
        draft: sqlite3.Row,
    ) -> dict[str, Any]:
        payload = self._draft_dict(connection, draft)
        return {
            "success": True,
            "bookingId": draft["booking_id"],
            "meetingId": draft["meeting_id"],
            "roomId": draft["room_id"],
            "roomName": payload["roomName"],
            "date": draft["booking_date"],
            "timeRange": f"{draft['start_time']}-{draft['end_time']}",
            "capacity": draft["capacity"],
            "theme": draft["theme"],
            "message": "会议室预约成功",
            "draft": payload,
        }


class MeetingRoomClient(Protocol):
    """Minimal client contract shared with future real API integration."""

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


class SandboxMeetingRoomClient:
    """Adapter exposing the same two operations as the future HTTP client."""

    def __init__(self, store: MeetingRoomStore):
        self.store = store

    def select_meet(self, **kwargs) -> dict[str, Any]:
        return self.store.list_rooms(**kwargs)

    def create(self, **kwargs) -> dict[str, Any]:
        return self.store.create_booking(**kwargs)

    def create_draft(self, **kwargs) -> dict[str, Any]:
        return self.store.create_draft(**kwargs)


class QueryMeetingRoomsInput(BaseModel):
    floor: str | None = Field(
        default=None,
        description="楼层，纯数字，例如7",
    )
    room: str | None = Field(
        default=None,
        description="会议室名称、编号或roomId，例如707或room-707",
    )
    date: str | None = Field(
        default=None,
        description="日期，格式yyyy/MM/dd",
    )
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
            self.floor = _validate_floor(self.floor)
        if self.room is not None:
            self.room = self.room.strip() or None
        if self.date is not None:
            self.date = _validate_date(self.date)
        if self.timeRange is not None:
            _parse_time_range(self.timeRange)
        if not any(
            (self.floor, self.room, self.date, self.timeRange)
        ):
            raise ValueError("floor、room、date或timeRange至少提供一项")
        return self


class BookMeetingRoomInput(BaseModel):
    roomId: str = Field(description="查询结果返回的会议室ID")
    floor: str = Field(description="楼层，纯数字，例如7")
    date: str = Field(description="预约日期，格式yyyy/MM/dd")
    timeRange: str = Field(description="预约时间段，格式HH:mm-HH:mm")
    capacity: int | None = Field(
        default=None,
        description="参会人数，默认5",
        ge=1,
    )
    theme: str | None = Field(
        default=None,
        description=(
            "会议主题；不提供时由服务端按“预约人姓名+预约的会议”生成"
        ),
    )

    @model_validator(mode="after")
    def validate_booking(self) -> "BookMeetingRoomInput":
        self.roomId = self.roomId.strip()
        if not self.roomId:
            raise ValueError("roomId不能为空")
        self.floor = _validate_floor(self.floor)
        self.date = _validate_date(self.date)
        _parse_time_range(self.timeRange)
        if self.theme is not None:
            self.theme = self.theme.strip() or None
        return self


def create_meeting_room_tools(
    client: MeetingRoomClient,
) -> list[BaseTool]:
    """Bind one meeting-room client to the two natural-language tools."""

    @tool(
        "queryMeetingRooms",
        args_schema=QueryMeetingRoomsInput,
        description=(
            "按楼层、房间、日期或时间任一线索查询会议室。返回真实占用信息、"
            "09:00-18:00半小时时间轴、连续空闲时段和适合所问时长的候选时间。"
            "capacity可选，时间查询时默认5人。本工具只查询，不创建预约。"
        ),
    )
    def query_meeting_rooms(
        floor: str | None = None,
        room: str | None = None,
        date: str | None = None,
        timeRange: str | None = None,
        capacity: int | None = None,
    ) -> str:
        timed_query = timeRange is not None
        result = client.select_meet(
            floor=floor,
            room_query=room,
            date=date,
            time_range=timeRange,
            capacity=(
                capacity or DEFAULT_MEETING_CAPACITY
            ) if timed_query else None,
            available_only=False,
        )
        return json.dumps(result, ensure_ascii=False)

    @tool(
        "bookMeetingRoom",
        args_schema=BookMeetingRoomInput,
        description=(
            "生成一张由用户最终确认的会议室预约卡片，本工具绝不创建预约。"
            "roomId、floor、date和timeRange必须提供。会先重新查询并检查冲突；"
            "用户必须在卡片中修改或确认，模型不得代替用户完成最后确认。"
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
        latest = client.select_meet(
            floor=floor,
            date=date,
            time_range=timeRange,
            capacity=actual_capacity,
            available_only=False,
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
                {"success": False, "message": "未找到指定会议室"},
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
        except (MeetingRoomConflictError, MeetingRoomError) as exc:
            result = {
                "success": False,
                "message": str(exc),
            }
        return json.dumps(result, ensure_ascii=False)

    return [query_meeting_rooms, book_meeting_room]


def _validate_floor(value: str) -> str:
    normalized = value.strip()
    if not normalized.isdigit():
        raise MeetingRoomError("楼层必须是纯数字")
    return str(int(normalized))


def _validate_date(value: str) -> str:
    normalized = value.strip()
    try:
        parsed = datetime.strptime(normalized, "%Y/%m/%d")
    except ValueError as exc:
        raise MeetingRoomError("日期格式必须为yyyy/MM/dd") from exc
    return parsed.strftime("%Y/%m/%d")


def _parse_time_range(value: str) -> tuple[str, str]:
    parts = value.strip().split("-")
    if len(parts) != 2:
        raise MeetingRoomError("时间段格式必须为HH:mm-HH:mm")
    normalized = []
    for part in parts:
        try:
            parsed = datetime.strptime(part.strip(), "%H:%M")
        except ValueError as exc:
            raise MeetingRoomError(
                "时间段格式必须为HH:mm-HH:mm"
            ) from exc
        normalized.append(parsed.strftime("%H:%M"))
    start_time, end_time = normalized
    if start_time >= end_time:
        raise MeetingRoomError("预约结束时间必须晚于开始时间")
    return start_time, end_time


def _validate_bookable_slot(
    date: str,
    start_time: str,
    end_time: str,
    *,
    now: datetime,
) -> None:
    start_minutes = _time_to_minutes(start_time)
    end_minutes = _time_to_minutes(end_time)
    if (
        start_minutes < WORKDAY_START_MINUTES
        or end_minutes > WORKDAY_END_MINUTES
    ):
        raise MeetingRoomError("会议室仅支持工作时段09:00-18:00预约")
    if (
        start_minutes % SLOT_MINUTES != 0
        or end_minutes % SLOT_MINUTES != 0
    ):
        raise MeetingRoomError("预约时间必须按30分钟整点或半点选择")
    localized_now = (
        now.replace(tzinfo=SHANGHAI_TIMEZONE)
        if now.tzinfo is None
        else now.astimezone(SHANGHAI_TIMEZONE)
    )
    booking_start = datetime.strptime(
        f"{date} {start_time}",
        "%Y/%m/%d %H:%M",
    ).replace(tzinfo=SHANGHAI_TIMEZONE)
    booking_end = datetime.strptime(
        f"{date} {end_time}",
        "%Y/%m/%d %H:%M",
    ).replace(tzinfo=SHANGHAI_TIMEZONE)
    current_slot_start = localized_now.replace(
        minute=localized_now.minute
        - localized_now.minute % SLOT_MINUTES,
        second=0,
        microsecond=0,
    )
    if booking_start < current_slot_start or booking_end <= localized_now:
        raise MeetingRoomError("不能预约过去的时间")


def _time_to_minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _overlaps(
    first_start: str,
    first_end: str,
    second_start: str,
    second_end: str,
) -> bool:
    return first_start < second_end and first_end > second_start


def _booking_dict(booking: sqlite3.Row) -> dict[str, Any]:
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


def _build_half_hour_timeline(
    bookings: list[sqlite3.Row],
    *,
    capacity_available: bool,
    start_minutes: int = WORKDAY_START_MINUTES,
) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for slot_start_minutes in range(
        start_minutes,
        WORKDAY_END_MINUTES,
        SLOT_MINUTES,
    ):
        end_minutes = slot_start_minutes + SLOT_MINUTES
        start_time = _minutes_to_time(slot_start_minutes)
        end_time = _minutes_to_time(end_minutes)
        booking = next(
            (
                candidate
                for candidate in bookings
                if _overlaps(
                    start_time,
                    end_time,
                    candidate["start_time"],
                    candidate["end_time"],
                )
            ),
            None,
        )
        available = capacity_available and booking is None
        timeline.append(
            {
                "start": start_time,
                "end": end_time,
                "timeRange": f"{start_time}-{end_time}",
                "available": available,
                "status": (
                    "available"
                    if available
                    else ("occupied" if booking is not None else "capacity")
                ),
                "booking": (
                    _booking_dict(booking) if booking is not None else None
                ),
            }
        )
    return timeline


def _current_slot_start_minutes(now: datetime) -> int:
    localized_now = (
        now.replace(tzinfo=SHANGHAI_TIMEZONE)
        if now.tzinfo is None
        else now.astimezone(SHANGHAI_TIMEZONE)
    )
    current_minutes = localized_now.hour * 60 + localized_now.minute
    rounded_down = current_minutes - current_minutes % SLOT_MINUTES
    return min(
        WORKDAY_END_MINUTES,
        max(WORKDAY_START_MINUTES, rounded_down),
    )


def _merge_available_slots(
    timeline: list[dict[str, Any]],
) -> list[str]:
    ranges: list[str] = []
    range_start: str | None = None
    for slot in timeline:
        if slot["available"] and range_start is None:
            range_start = slot["start"]
        if not slot["available"] and range_start is not None:
            ranges.append(f"{range_start}-{slot['start']}")
            range_start = None
    if range_start is not None:
        ranges.append(
            f"{range_start}-{_minutes_to_time(WORKDAY_END_MINUTES)}"
        )
    return ranges


def _matching_duration_ranges(
    timeline: list[dict[str, Any]],
    *,
    duration_minutes: int,
) -> list[str]:
    slots_needed = max(
        1,
        (duration_minutes + SLOT_MINUTES - 1) // SLOT_MINUTES,
    )
    matches: list[str] = []
    for index in range(0, len(timeline) - slots_needed + 1):
        window = timeline[index:index + slots_needed]
        if all(slot["available"] for slot in window):
            matches.append(
                f"{window[0]['start']}-{window[-1]['end']}"
            )
    return matches


def _range_duration_minutes(
    requested_slot: tuple[str, str],
) -> int:
    return (
        _time_to_minutes(requested_slot[1])
        - _time_to_minutes(requested_slot[0])
    )


def _time_to_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _minutes_to_time(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"
