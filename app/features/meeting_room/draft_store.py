"""Local persistence for human-confirmation drafts, never room inventory."""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from app.persistence.conversations import DEFAULT_DB_PATH
from app.persistence.database import (
    database_display_name,
    ensure_mysql_schema,
    is_mysql_target,
    mysql_connection,
    sqlite_connection,
)
from app.features.meeting_room.domain import (
    DRAFT_EXPIRY_MINUTES,
    SHANGHAI_TIMEZONE,
    MeetingRoomConflictError,
    MeetingRoomDraftNotFoundError,
    MeetingRoomDraftStateError,
    MeetingRoomError,
    MeetingRoomNotFoundError,
    parse_time_range,
    validate_bookable_slot,
    validate_date,
    validate_floor,
)
from app.features.meeting_room.gateway import MeetingRoomGateway
from app.integrations.mock_sandbox.client import (
    MockSandboxConflictError,
    MockSandboxError,
)


class MeetingRoomDraftStore:
    """Persist only UI confirmation state while external APIs own all facts."""

    def __init__(
        self,
        gateway: MeetingRoomGateway,
        db_path: Path | str | None = None,
        *,
        now_factory=None,
        booked_by: str = "沙箱访客",
        booked_by_provider: Callable[[], str] | None = None,
    ):
        if db_path is None and os.getenv("XIAOYUAN_DB_PATH"):
            db_path = DEFAULT_DB_PATH
        self._mysql = is_mysql_target(db_path)
        self._sqlite_path = Path(db_path) if db_path is not None else None
        self.db_path = database_display_name(db_path)
        self.gateway = gateway
        self._now_factory = now_factory or (
            lambda: datetime.now(SHANGHAI_TIMEZONE)
        )
        self._booked_by = booked_by.strip() or "沙箱访客"
        self._booked_by_provider = booked_by_provider
        self._write_lock = threading.RLock()
        self._init_schema()

    def _connect(self):
        if self._mysql:
            return mysql_connection()
        assert self._sqlite_path is not None
        return sqlite_connection(self._sqlite_path)

    def _init_schema(self) -> None:
        if self._mysql:
            ensure_mysql_schema()
            return
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meeting_room_booking_drafts (
                    draft_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    round_no INTEGER,
                    room_id TEXT NOT NULL,
                    room_name TEXT NOT NULL,
                    floor TEXT NOT NULL,
                    booking_date TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    capacity INTEGER NOT NULL CHECK (capacity > 0),
                    theme TEXT NOT NULL,
                    booked_by TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'confirmed', 'cancelled', 'expired')
                    ),
                    booking_id TEXT,
                    meeting_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_meeting_room_drafts_session
                ON meeting_room_booking_drafts(session_id, round_no, created_at);
                """
            )

    def list_rooms(self, **kwargs) -> dict[str, Any]:
        try:
            return self.gateway.select_meet(**kwargs)
        except MockSandboxError as exc:
            raise MeetingRoomError(str(exc)) from exc

    def create_draft(
        self,
        *,
        room_id: str,
        floor: str,
        date: str,
        time_range: str,
        capacity: int,
        theme: str | None,
        session_id: str | None = None,
        round_no: int | None = None,
    ) -> dict[str, Any]:
        room_id, floor, date, start_time, end_time = self._validated_values(
            room_id, floor, date, time_range, capacity
        )
        now = self._now_factory()
        validate_bookable_slot(date, start_time, end_time, now=now)
        room = self._validate_external_room(
            room_id=room_id,
            floor=floor,
            date=date,
            start_time=start_time,
            end_time=end_time,
            capacity=capacity,
        )
        draft_id = uuid.uuid4().hex
        created_at = now.isoformat(timespec="seconds")
        expires_at = (now + timedelta(minutes=DRAFT_EXPIRY_MINUTES)).isoformat(
            timespec="seconds"
        )
        booked_by = self._current_booked_by()
        normalized_theme = (theme or "").strip() or f"{booked_by}预定的会议"
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO meeting_room_booking_drafts(
                    draft_id, session_id, round_no, room_id, room_name, floor,
                    booking_date, start_time, end_time, capacity, theme,
                    booked_by, status, booking_id, meeting_id,
                    created_at, updated_at, expires_at
                )
                VALUES(
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'pending', NULL, NULL, ?, ?, ?
                )
                """,
                (
                    draft_id,
                    session_id,
                    round_no,
                    room_id,
                    room["room_name"],
                    floor,
                    date,
                    start_time,
                    end_time,
                    capacity,
                    normalized_theme,
                    booked_by,
                    created_at,
                    created_at,
                    expires_at,
                ),
            )
        return self.get_draft(draft_id)

    def _current_booked_by(self) -> str:
        if self._booked_by_provider is None:
            return self._booked_by
        return self._booked_by_provider().strip() or "沙箱访客"

    def default_booking_theme(self) -> str:
        """Resolve the current authenticated user's deterministic default theme."""
        return f"{self._current_booked_by()}预定的会议"

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        with self._write_lock, self._connect() as connection:
            row = self._expire_if_needed(
                connection,
                self._load_draft(connection, draft_id),
            )
            return self._draft_dict(row)

    def list_drafts(self, *, session_id: str) -> list[dict[str, Any]]:
        with self._write_lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM meeting_room_booking_drafts
                WHERE session_id = ?
                ORDER BY round_no, created_at
                """,
                (session_id,),
            ).fetchall()
            return [
                self._draft_dict(self._expire_if_needed(connection, row))
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
        room_id, floor, date, start_time, end_time = self._validated_values(
            room_id, floor, date, time_range, capacity
        )
        now = self._now_factory()
        validate_bookable_slot(date, start_time, end_time, now=now)
        with self._write_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            draft = self._expire_if_needed(
                connection,
                self._load_draft(connection, draft_id, for_update=True),
            )
            if draft["status"] != "pending":
                raise MeetingRoomDraftStateError("该预约卡片已失效，无法修改")
            room = self._validate_external_room(
                room_id=room_id,
                floor=floor,
                date=date,
                start_time=start_time,
                end_time=end_time,
                capacity=capacity,
            )
            normalized_theme = (theme or "").strip() or (
                f"{draft['booked_by']}预定的会议"
            )
            connection.execute(
                """
                UPDATE meeting_room_booking_drafts
                SET room_id = ?, room_name = ?, floor = ?, booking_date = ?,
                    start_time = ?, end_time = ?, capacity = ?, theme = ?,
                    updated_at = ?
                WHERE draft_id = ?
                """,
                (
                    room_id,
                    room["room_name"],
                    floor,
                    date,
                    start_time,
                    end_time,
                    capacity,
                    normalized_theme,
                    now.isoformat(timespec="seconds"),
                    draft_id,
                ),
            )
        return self.get_draft(draft_id)

    def confirm_draft(self, draft_id: str) -> dict[str, Any]:
        """Perform the external write only after the human clicks confirm."""
        with self._write_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            draft = self._expire_if_needed(
                connection,
                self._load_draft(connection, draft_id, for_update=True),
            )
            if draft["status"] == "confirmed":
                return self._confirmed_result(draft)
            if draft["status"] != "pending":
                raise MeetingRoomDraftStateError(
                    "该预约卡片已取消，无法确认"
                    if draft["status"] == "cancelled"
                    else "该预约卡片已失效，无法确认"
                )
            now = self._now_factory()
            validate_bookable_slot(
                draft["booking_date"],
                draft["start_time"],
                draft["end_time"],
                now=now,
            )
            self._validate_external_room(
                room_id=draft["room_id"],
                floor=draft["floor"],
                date=draft["booking_date"],
                start_time=draft["start_time"],
                end_time=draft["end_time"],
                capacity=int(draft["capacity"]),
            )
            try:
                remote = self.gateway.create(
                    room_id=draft["room_id"],
                    floor=draft["floor"],
                    date=draft["booking_date"],
                    time_range=f"{draft['start_time']}-{draft['end_time']}",
                    capacity=int(draft["capacity"]),
                    theme=draft["theme"],
                )
            except MockSandboxConflictError as exc:
                raise MeetingRoomConflictError(str(exc)) from exc
            except MockSandboxError as exc:
                raise MeetingRoomError(str(exc)) from exc
            updated_at = now.isoformat(timespec="seconds")
            connection.execute(
                """
                UPDATE meeting_room_booking_drafts
                SET status = 'confirmed', booking_id = ?, meeting_id = ?,
                    room_name = ?, updated_at = ?
                WHERE draft_id = ?
                """,
                (
                    str(remote["bookingId"]),
                    str(remote["meetingId"]),
                    remote.get("roomName") or draft["room_name"],
                    updated_at,
                    draft_id,
                ),
            )
            confirmed = self._load_draft(connection, draft_id)
            return {
                **remote,
                "success": True,
                "draft": self._draft_dict(confirmed),
            }

    def cancel_draft(self, draft_id: str) -> dict[str, Any]:
        with self._write_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            draft = self._expire_if_needed(
                connection,
                self._load_draft(connection, draft_id, for_update=True),
            )
            if draft["status"] == "cancelled":
                return self._draft_dict(draft)
            if draft["status"] != "pending":
                raise MeetingRoomDraftStateError("该预约卡片已无法取消")
            connection.execute(
                """
                UPDATE meeting_room_booking_drafts
                SET status = 'cancelled', updated_at = ?
                WHERE draft_id = ?
                """,
                (self._now_factory().isoformat(timespec="seconds"), draft_id),
            )
            return self._draft_dict(self._load_draft(connection, draft_id))

    def _validated_values(
        self,
        room_id: str,
        floor: str,
        date: str,
        time_range: str,
        capacity: int,
    ) -> tuple[str, str, str, str, str]:
        normalized_room_id = room_id.strip()
        if not normalized_room_id:
            raise MeetingRoomError("会议室ID不能为空")
        if capacity < 1:
            raise MeetingRoomError("参会人数必须大于0")
        start_time, end_time = parse_time_range(time_range)
        return (
            normalized_room_id,
            validate_floor(floor),
            validate_date(date),
            start_time,
            end_time,
        )

    def _validate_external_room(
        self,
        *,
        room_id: str,
        floor: str,
        date: str,
        start_time: str,
        end_time: str,
        capacity: int,
    ) -> dict[str, Any]:
        try:
            result = self.gateway.select_meet(
                floor=floor,
                room_query=room_id,
                date=date,
                time_range=f"{start_time}-{end_time}",
                capacity=capacity,
                available_only=False,
            )
        except MockSandboxError as exc:
            raise MeetingRoomError(str(exc)) from exc
        room = next(
            (
                candidate
                for candidate in result.get("rooms", [])
                if candidate.get("roomId") == room_id
            ),
            None,
        )
        if room is None:
            raise MeetingRoomNotFoundError("外部系统中没有该会议室")
        if not room.get("available", False):
            raise MeetingRoomConflictError(
                "该会议室在当前时段已被预订或容量不足"
            )
        return {
            "room_id": room_id,
            "room_name": room.get("roomName") or room_id,
        }

    def _load_draft(
        self,
        connection: Any,
        draft_id: str,
        *,
        for_update: bool = False,
    ) -> Any:
        row = connection.execute(
            "SELECT * FROM meeting_room_booking_drafts WHERE draft_id = ?"
            + (" FOR UPDATE" if self._mysql and for_update else ""),
            (draft_id,),
        ).fetchone()
        if row is None:
            raise MeetingRoomDraftNotFoundError("没有找到该预约卡片")
        return row

    def _expire_if_needed(self, connection: Any, draft: Any) -> Any:
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

    @staticmethod
    def _draft_dict(draft: Any) -> dict[str, Any]:
        return {
            "type": "meetingRoomBookingDraft",
            "draftId": draft["draft_id"],
            "sessionId": draft["session_id"],
            "round": draft["round_no"],
            "roomId": draft["room_id"],
            "roomName": draft["room_name"],
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

    def _confirmed_result(self, draft: Any) -> dict[str, Any]:
        return {
            "success": True,
            "bookingId": draft["booking_id"],
            "meetingId": draft["meeting_id"],
            "roomId": draft["room_id"],
            "roomName": draft["room_name"],
            "date": draft["booking_date"],
            "timeRange": f"{draft['start_time']}-{draft['end_time']}",
            "capacity": draft["capacity"],
            "theme": draft["theme"],
            "message": "会议室预约成功",
            "draft": self._draft_dict(draft),
        }
