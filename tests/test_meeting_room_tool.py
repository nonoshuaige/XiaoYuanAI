from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from meeting_room_tool import (
    DEFAULT_MEETING_CAPACITY,
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

    def create_draft(self, **kwargs):
        self.calls.append("create_draft")
        return super().create_draft(**kwargs)


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
        self.assertIn("生成待确认预约卡片", skill.instructions)
        self.assertIn("不要求用户必须先提供楼层", skill.instructions)
        self.assertIn("suggestedTimeRanges", skill.instructions)
        self.assertIn("预约人姓名+预约的会议", skill.instructions)
        self.assertIn("将theme留空", skill.instructions)
        self.assertIn("模型也不能代替用户执行最终预约", skill.instructions)
        self.assertIn("服务端才会再次查询冲突", skill.instructions)

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

        self.assertEqual(result["capacity"], DEFAULT_MEETING_CAPACITY)
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
        self.assertIn("13:30-14:00", room_707["suggestedTimeRanges"])

    def test_date_only_query_returns_all_floors_and_half_hour_timeline(self):
        query_tool, _ = create_meeting_room_tools(
            SandboxMeetingRoomClient(self.store)
        )

        result = json.loads(
            query_tool.invoke({"date": "2026/07/28"})
        )

        self.assertEqual(len(result["rooms"]), 9)
        self.assertEqual(result["scheduleWindow"], "09:00-18:00")
        self.assertEqual(result["displayWindow"], "13:30-18:00")
        self.assertEqual(result["slotMinutes"], 30)
        self.assertTrue(
            all(len(room["timeline"]) == 9 for room in result["rooms"])
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
            ["13:30-18:00"],
        )

    def test_today_timeline_starts_at_current_half_hour_slot(self):
        cases = (
            ("2026-07-28T10:29:00+08:00", "10:00-10:30"),
            ("2026-07-28T10:30:00+08:00", "10:30-11:00"),
        )
        for index, (now_value, first_slot) in enumerate(cases):
            with self.subTest(now=now_value):
                store = MeetingRoomStore(
                    Path(self.temp_dir.name) / f"slot-{index}.db",
                    now_factory=lambda value=now_value: datetime.fromisoformat(
                        value
                    ),
                )
                result = store.list_rooms(
                    room_query="707",
                    date="2026/07/28",
                )
                room = result["rooms"][0]
                self.assertEqual(room["timeline"][0]["timeRange"], first_slot)
                self.assertEqual(room["occupied"], [])

    def test_query_requires_at_least_one_clue(self):
        query_tool, _ = create_meeting_room_tools(
            SandboxMeetingRoomClient(self.store)
        )

        with self.assertRaises(ValidationError):
            query_tool.invoke({})

    def test_booking_tool_requeries_then_creates_draft_without_booking(self):
        tracking_client = TrackingMeetingRoomClient(self.store)
        _, booking_tool = create_meeting_room_tools(tracking_client)

        result = json.loads(
            booking_tool.invoke(
                {
                    "roomId": "room-707",
                    "floor": "7",
                    "date": "2026/07/29",
                    "timeRange": "09:00-10:00",
                    "capacity": 5,
                    "theme": "需求讨论",
                }
            )
        )

        self.assertEqual(tracking_client.calls, ["select", "create_draft"])
        self.assertTrue(result["success"])
        self.assertEqual(result["type"], "meetingRoomBookingDraft")
        self.assertEqual(result["roomId"], "room-707")
        self.assertTrue(result["draftId"])
        self.assertIsNone(result["bookingId"])
        self.assertIsNone(result["meetingId"])
        room = next(
            item
            for item in self.store.list_rooms(
                room_query="707",
                date="2026/07/29",
            )["rooms"]
            if item["roomId"] == "room-707"
        )
        self.assertEqual(room["occupied"], [])

    def test_booking_tool_schema_has_no_model_confirmation_parameter(self):
        _, booking_tool = create_meeting_room_tools(
            SandboxMeetingRoomClient(self.store)
        )

        self.assertNotIn(
            "confirmed",
            booking_tool.args_schema.model_json_schema()["properties"],
        )

    def test_server_generates_default_theme_from_trusted_booker(self):
        created = self.store.create_booking(
            room_id="room-711",
            floor="7",
            date="2026/07/29",
            time_range="16:00-17:00",
            booked_by=" 张三 ",
        )

        self.assertEqual(created["theme"], "张三预约的会议")
        room = next(
            room
            for room in self.store.list_rooms(
                room_query="711",
                date="2026/07/29",
            )["rooms"]
            if room["roomId"] == "room-711"
        )
        self.assertEqual(room["occupied"][0]["theme"], "张三预约的会议")
        self.assertEqual(room["occupied"][0]["bookedBy"], "张三")

    def test_booking_tool_leaves_default_theme_to_server_draft(self):
        _, booking_tool = create_meeting_room_tools(
            SandboxMeetingRoomClient(self.store)
        )

        result = json.loads(
            booking_tool.invoke(
                {
                    "roomId": "room-711",
                    "floor": "7",
                    "date": "2026/07/29",
                    "timeRange": "16:00-17:00",
                }
            )
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["theme"], "沙箱访客预约的会议")
        self.assertEqual(result["status"], "pending")

    def test_human_confirmation_is_the_only_final_write_and_is_idempotent(self):
        draft = self.store.create_draft(
            room_id="room-711",
            floor="7",
            date="2026/07/29",
            time_range="16:00-17:00",
            theme="人工确认",
        )
        before = self.store.list_rooms(
            room_query="711",
            date="2026/07/29",
        )["rooms"][0]["occupied"]
        self.assertEqual(before, [])

        confirmed = self.store.confirm_draft(draft["draftId"])
        repeated = self.store.confirm_draft(draft["draftId"])

        self.assertTrue(confirmed["success"])
        self.assertEqual(confirmed["bookingId"], repeated["bookingId"])
        self.assertEqual(confirmed["draft"]["status"], "confirmed")

    def test_store_rejects_past_outside_workday_and_non_half_hour(self):
        invalid_slots = (
            ("2026/07/28", "13:00-13:30", "过去"),
            ("2026/07/29", "08:30-09:30", "09:00-18:00"),
            ("2026/07/29", "17:30-18:30", "09:00-18:00"),
            ("2026/07/29", "09:15-10:00", "30分钟"),
        )
        for date, time_range, message in invalid_slots:
            with self.subTest(date=date, time_range=time_range):
                with self.assertRaisesRegex(ValueError, message):
                    self.store.create_booking(
                        room_id="room-711",
                        floor="7",
                        date=date,
                        time_range=time_range,
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
