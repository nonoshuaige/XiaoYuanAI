from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from meeting_room_tool import (
    MeetingRoomConflictError,
    MeetingRoomStore,
    SandboxMeetingRoomClient,
    create_meeting_room_tools,
)
from meeting_room_skill import create_meeting_room_skill


FIXED_NOW = datetime.fromisoformat("2026-07-28T13:30:00+08:00")


class TrackingMeetingRoomClient(SandboxMeetingRoomClient):
    def __init__(self, store: MeetingRoomStore):
        super().__init__(store)
        self.calls: list[str] = []

    def select_meet(self, **kwargs):
        self.calls.append("select")
        return super().select_meet(**kwargs)

    def create(self, **kwargs):
        self.calls.append("create")
        return super().create(**kwargs)


class MeetingRoomToolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = MeetingRoomStore(
            Path(self.temp_dir.name) / "rooms.db",
            now_factory=lambda: FIXED_NOW,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_floor_only_query_returns_every_room_without_capacity_filter(self):
        result = self.store.list_rooms(floor="7")

        self.assertTrue(result["success"])
        self.assertEqual(len(result["rooms"]), 3)
        self.assertEqual(
            {room["roomId"] for room in result["rooms"]},
            {"room-707", "room-708", "room-711"},
        )

    def test_tools_exist_without_loading_sandbox_seed_data(self):
        production_store = MeetingRoomStore(
            Path(self.temp_dir.name) / "production-rooms.db",
            now_factory=lambda: FIXED_NOW,
            seed_sandbox_data=False,
        )
        tools = create_meeting_room_tools(
            SandboxMeetingRoomClient(production_store)
        )

        self.assertEqual(
            [registered_tool.name for registered_tool in tools],
            ["queryMeetingRooms", "bookMeetingRoom"],
        )
        self.assertEqual(
            production_store.list_rooms(floor="7")["rooms"],
            [],
        )

    def test_skill_groups_query_booking_and_workflow_constraints(self):
        skill = create_meeting_room_skill(
            SandboxMeetingRoomClient(self.store)
        )

        self.assertEqual(skill.name, "meeting-room-booking")
        self.assertEqual(
            [registered_tool.name for registered_tool in skill.tools],
            ["queryMeetingRooms", "bookMeetingRoom"],
        )
        self.assertIn("用户选择会议室只代表选中候选", skill.instructions)
        self.assertIn("不要求用户必须先提供楼层", skill.instructions)
        self.assertIn("suggestedTimeRanges", skill.instructions)
        self.assertIn("confirmed才可设为true", skill.instructions)
        self.assertIn("重新查询会议室并检查冲突", skill.instructions)

    def test_timed_tool_query_keeps_conflicts_and_suggests_alternatives(self):
        query_tool, _ = create_meeting_room_tools(
            SandboxMeetingRoomClient(self.store)
        )

        result = json.loads(
            query_tool.invoke(
                {
                    "floor": "7",
                    "date": "2026/07/28",
                    "timeRange": "09:30-10:00",
                }
            )
        )

        self.assertEqual(result["capacity"], 5)
        self.assertEqual(
            {
                room["roomId"]
                for room in result["rooms"]
                if room["available"]
            },
            {"room-708", "room-711"},
        )
        room_707 = next(
            room for room in result["rooms"]
            if room["roomId"] == "room-707"
        )
        self.assertFalse(room_707["available"])
        self.assertIn("10:00-10:30", room_707["suggestedTimeRanges"])

    def test_date_only_query_returns_all_floors_and_half_hour_timeline(self):
        query_tool, _ = create_meeting_room_tools(
            SandboxMeetingRoomClient(self.store)
        )

        result = json.loads(
            query_tool.invoke({"date": "2026/07/28"})
        )

        self.assertEqual(len(result["rooms"]), 9)
        self.assertEqual(result["scheduleWindow"], "09:00-18:00")
        self.assertEqual(result["slotMinutes"], 30)
        self.assertTrue(
            all(len(room["timeline"]) == 18 for room in result["rooms"])
        )

    def test_room_only_query_finds_room_without_floor(self):
        query_tool, _ = create_meeting_room_tools(
            SandboxMeetingRoomClient(self.store)
        )

        result = json.loads(query_tool.invoke({"room": "707"}))

        self.assertEqual(
            [room["roomId"] for room in result["rooms"]],
            ["room-707"],
        )
        self.assertEqual(
            result["rooms"][0]["availableTimeRanges"],
            ["10:00-18:00"],
        )

    def test_query_requires_at_least_one_clue(self):
        query_tool, _ = create_meeting_room_tools(
            SandboxMeetingRoomClient(self.store)
        )

        with self.assertRaises(ValidationError):
            query_tool.invoke({})

    def test_booking_requeries_before_create_and_returns_real_ids(self):
        tracking_client = TrackingMeetingRoomClient(self.store)
        _, booking_tool = create_meeting_room_tools(tracking_client)

        result = json.loads(
            booking_tool.invoke(
                {
                    "roomId": "room-707",
                    "floor": "7",
                    "date": "2026/07/29",
                    "timeRange": "09:00-10:00",
                    "confirmed": True,
                    "capacity": 5,
                    "theme": "需求讨论",
                }
            )
        )

        self.assertEqual(tracking_client.calls, ["select", "create"])
        self.assertTrue(result["success"])
        self.assertEqual(result["roomId"], "room-707")
        self.assertTrue(result["bookingId"])
        self.assertTrue(result["meetingId"].startswith("NMS20260729"))

    def test_booking_requires_explicit_confirmation(self):
        _, booking_tool = create_meeting_room_tools(
            SandboxMeetingRoomClient(self.store)
        )

        with self.assertRaises(ValidationError):
            booking_tool.invoke(
                {
                    "roomId": "room-707",
                    "floor": "7",
                    "date": "2026/07/29",
                    "timeRange": "09:00-10:00",
                    "confirmed": False,
                }
            )

    def test_store_rejects_overlap_but_allows_adjacent_booking(self):
        first = self.store.create_booking(
            room_id="room-707",
            floor="7",
            date="2026/07/29",
            time_range="09:00-10:00",
            theme="第一场",
        )
        adjacent = self.store.create_booking(
            room_id="room-707",
            floor="7",
            date="2026/07/29",
            time_range="10:00-11:00",
            theme="第二场",
        )

        self.assertTrue(first["success"])
        self.assertTrue(adjacent["success"])
        with self.assertRaisesRegex(
            MeetingRoomConflictError,
            "当前时段已被预订",
        ):
            self.store.create_booking(
                room_id="room-707",
                floor="7",
                date="2026/07/29",
                time_range="09:30-10:30",
                theme="冲突场次",
            )


if __name__ == "__main__":
    unittest.main()
