from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from app.features.meeting_room.domain import MeetingRoomConflictError
from app.features.meeting_room.draft_store import MeetingRoomDraftStore
from app.features.meeting_room.skill import create_meeting_room_skill
from app.features.meeting_room.tools import MeetingRoomAgentClient, create_meeting_room_tools


FIXED_NOW = datetime.fromisoformat("2026-07-28T13:42:00+08:00")


class FakeExternalMeetingGateway:
    def __init__(self):
        self.select_calls: list[dict] = []
        self.create_calls: list[dict] = []
        self.available = True

    def select_meet(self, **kwargs):
        self.select_calls.append(kwargs)
        room_id = kwargs.get("room_query") or "165"
        return {
            "success": True,
            "source": "mock-sandbox",
            "date": kwargs.get("date") or "2026/07/28",
            "timeRange": kwargs.get("time_range"),
            "capacity": kwargs.get("capacity"),
            "scheduleWindow": "09:00-18:00",
            "displayWindow": "13:30-18:00",
            "slotMinutes": 30,
            "rooms": [
                {
                    "roomId": room_id,
                    "roomName": "707会议室",
                    "floor": "7F",
                    "capacity": 30,
                    "equipment": ["视频会议"],
                    "available": self.available,
                    "occupied": [],
                    "timeline": [],
                    "availableTimeRanges": ["13:30-18:00"],
                    "suggestedTimeRanges": ["13:30-14:30"],
                }
            ],
        }

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return {
            "success": True,
            "bookingId": "reserve-1",
            "meetingId": "meeting-1",
            "roomId": kwargs["room_id"],
            "roomName": "707会议室",
            "date": kwargs["date"],
            "timeRange": kwargs["time_range"],
            "capacity": kwargs["capacity"],
            "theme": kwargs["theme"],
            "message": "会议室预约成功",
            "source": "mock-sandbox",
        }


class MeetingRoomToolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.gateway = FakeExternalMeetingGateway()
        self.store = MeetingRoomDraftStore(
            self.gateway,
            Path(self.temp_dir.name) / "drafts.db",
            now_factory=lambda: FIXED_NOW,
            booked_by="程少伟",
        )
        self.client = MeetingRoomAgentClient(self.gateway, self.store)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_local_database_contains_drafts_only(self):
        with sqlite3.connect(Path(self.temp_dir.name) / "drafts.db") as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertIn("meeting_room_booking_drafts", tables)
        self.assertNotIn("meeting_rooms", tables)
        self.assertNotIn("meeting_room_bookings", tables)
        self.assertNotIn("people", tables)

    def test_skill_groups_external_search_and_confirmation_draft(self):
        skill = create_meeting_room_skill(self.client)
        self.assertEqual(skill.name, "meeting-room-booking")
        self.assertEqual(
            skill.tool_names,
            ("queryMeetingRooms", "bookMeetingRoom"),
        )
        self.assertIn("模型也不能代替用户执行最终预约", skill.instructions)

    def test_query_requires_at_least_one_clue(self):
        query_tool, _ = create_meeting_room_tools(self.client)
        with self.assertRaises(ValidationError):
            query_tool.invoke({})

    def test_query_delegates_to_external_gateway(self):
        query_tool, _ = create_meeting_room_tools(self.client)
        result = json.loads(
            query_tool.invoke(
                {
                    "floor": "7",
                    "date": "2026/07/29",
                    "timeRange": "14:00-15:00",
                }
            )
        )
        self.assertEqual(result["source"], "mock-sandbox")
        self.assertEqual(self.gateway.select_calls[-1]["capacity"], 5)

    def test_booking_tool_creates_only_local_confirmation_draft(self):
        _, booking_tool = create_meeting_room_tools(self.client)
        result = json.loads(
            booking_tool.invoke(
                {
                    "roomId": "165",
                    "floor": "7",
                    "date": "2026/07/29",
                    "timeRange": "14:00-15:00",
                    "theme": "项目联调",
                }
            )
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["type"], "meetingRoomBookingDraft")
        self.assertEqual(result["roomName"], "707会议室")
        self.assertEqual(self.gateway.create_calls, [])

    def test_human_confirmation_is_only_external_write_and_is_idempotent(self):
        draft = self.store.create_draft(
            room_id="165",
            floor="7",
            date="2026/07/29",
            time_range="14:00-15:00",
            capacity=5,
            theme="项目联调",
        )
        self.assertEqual(self.gateway.create_calls, [])

        confirmed = self.store.confirm_draft(draft["draftId"])
        repeated = self.store.confirm_draft(draft["draftId"])

        self.assertEqual(len(self.gateway.create_calls), 1)
        self.assertEqual(confirmed["bookingId"], repeated["bookingId"])
        self.assertEqual(confirmed["draft"]["status"], "confirmed")

    def test_external_conflict_prevents_draft(self):
        self.gateway.available = False
        with self.assertRaises(MeetingRoomConflictError):
            self.store.create_draft(
                room_id="165",
                floor="7",
                date="2026/07/29",
                time_range="14:00-15:00",
                capacity=5,
                theme=None,
            )

    def test_pending_draft_can_be_updated_and_cancelled(self):
        draft = self.store.create_draft(
            room_id="165",
            floor="7",
            date="2026/07/29",
            time_range="14:00-15:00",
            capacity=5,
            theme=None,
        )
        updated = self.store.update_draft(
            draft["draftId"],
            room_id="166",
            floor="7",
            date="2026/07/29",
            time_range="15:00-16:00",
            capacity=8,
            theme="修改后的主题",
        )
        cancelled = self.store.cancel_draft(draft["draftId"])
        repeated = self.store.cancel_draft(draft["draftId"])

        self.assertEqual(updated["roomId"], "166")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(repeated["status"], "cancelled")

    def test_draft_expires_after_thirty_minutes(self):
        clock = [FIXED_NOW]
        store = MeetingRoomDraftStore(
            self.gateway,
            Path(self.temp_dir.name) / "expiring.db",
            now_factory=lambda: clock[0],
        )
        draft = store.create_draft(
            room_id="165",
            floor="7",
            date="2026/07/29",
            time_range="14:00-15:00",
            capacity=5,
            theme=None,
        )
        clock[0] += timedelta(minutes=30)
        self.assertEqual(store.get_draft(draft["draftId"])["status"], "expired")

    def test_booking_time_rules_are_local_transaction_validation_only(self):
        invalid = (
            ("2026/07/28", "13:00-13:30", "过去"),
            ("2026/07/29", "08:30-09:30", "09:00-18:00"),
            ("2026/07/29", "09:15-10:00", "30分钟"),
        )
        for date, time_range, message in invalid:
            with self.subTest(date=date, time_range=time_range):
                with self.assertRaisesRegex(ValueError, message):
                    self.store.create_draft(
                        room_id="165",
                        floor="7",
                        date=date,
                        time_range=time_range,
                        capacity=5,
                        theme=None,
                    )


if __name__ == "__main__":
    unittest.main()
