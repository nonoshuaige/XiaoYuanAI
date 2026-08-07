from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from app.features.meeting_room.domain import (
    DEFAULT_MEETING_DURATION_MINUTES,
    MeetingRoomConflictError,
)
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
        booking = {
            "timeRange": "14:00-15:00",
            "bookedBy": "李小明",
        }
        return {
            "success": True,
            "source": "mock-sandbox",
            "date": kwargs.get("date") or "2026/07/28",
            "timeRange": kwargs.get("time_range"),
            "capacity": kwargs.get("capacity"),
            "scheduleWindow": "09:00-18:30",
            "displayWindow": "13:30-18:30",
            "slotMinutes": 30,
            "rooms": [
                {
                    "roomId": room_id,
                    "roomName": "707会议室",
                    "floor": "7F",
                    "capacity": 30,
                    "equipment": ["视频会议"],
                    "available": self.available,
                    "occupied": [] if self.available else [booking],
                    "timeline": [
                        {
                            "start": "13:30",
                            "end": "14:00",
                            "timeRange": "13:30-14:00",
                            "available": self.available,
                            "status": "available" if self.available else "occupied",
                            "booking": None if self.available else booking,
                        },
                        {
                            "start": "14:00",
                            "end": "14:30",
                            "timeRange": "14:00-14:30",
                            "available": self.available,
                            "status": "available" if self.available else "occupied",
                            "booking": None if self.available else booking,
                        },
                        {
                            "start": "14:30",
                            "end": "15:00",
                            "timeRange": "14:30-15:00",
                            "available": self.available,
                            "status": "available" if self.available else "occupied",
                            "booking": None if self.available else booking,
                        },
                    ],
                    "availableTimeRanges": ["13:30-18:30"],
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

    def test_skill_groups_unified_search_and_booking_tools(self):
        skill = create_meeting_room_skill(self.client)
        self.assertEqual(skill.name, "meeting-room-assistant")
        self.assertEqual(
            skill.tool_names,
            ("search_meeting_rooms", "book_meeting_room"),
        )
        self.assertEqual(
            skill.description,
            "查询会议室静态信息和单日安排，并创建待确认预约卡片。",
        )
        self.assertIn("推送卡片不代表预约成功", skill.instructions)
        self.assertIn("不属于模型工具能力", skill.instructions)
        self.assertIn("808会议室在8楼", skill.instructions)
        self.assertIn("1101会议室在11楼", skill.instructions)
        self.assertIn("不要为了判断楼层调用工具", skill.instructions)
        self.assertIn("该规则只适用于会议室名称或房间号", skill.instructions)
        self.assertIn("一轮最多查询5个楼层", skill.instructions)
        self.assertIn("所有只读查询统一调用search_meeting_rooms", skill.instructions)
        self.assertIn("逐层过滤并返回交集", skill.instructions)
        self.assertIn("date是可修改的查询和预约日期", skill.instructions)
        self.assertIn("09:00-18:30", skill.instructions)
        self.assertIn("楼层是查询工具唯一必填的业务条件", skill.instructions)
        self.assertIn("由你自行判断", skill.instructions)
        self.assertIn("search_meeting_rooms.time`传空对象", skill.instructions)
        self.assertNotIn("必须立即", skill.instructions)
        self.assertIn("时长默认60分钟，人数默认5人", skill.instructions)
        self.assertIn("禁止追问", skill.instructions)
        self.assertIn("预订多长时间", skill.instructions)
        self.assertIn("多少人参加", skill.instructions)
        self.assertIn("自行选择合适、简短、精炼的说法", skill.instructions)
        self.assertIn("让用户看清可用性并自行选择", skill.instructions)
        self.assertIn("否则不要替用户选择房间或时段", skill.instructions)
        self.assertIn("裁剪房间和字段", skill.instructions)
        self.assertIn("明确指定房间时只回答该房间", skill.instructions)
        self.assertIn("只问日程时不附加", skill.instructions)
        self.assertIn("容量或设备", skill.instructions)
        self.assertIn("除非用户要求比较或查看备选", skill.instructions)
        self.assertEqual(DEFAULT_MEETING_DURATION_MINUTES, 60)

    def test_tool_descriptions_are_concise_and_preserve_query_dimensions(self):
        search_tool, booking_tool = create_meeting_room_tools(self.client)

        self.assertIn("单楼层", search_tool.description)
        self.assertIn("逐层筛选", search_tool.description)
        self.assertIn("一次调用完成复合查询", search_tool.description)
        self.assertIn("重查指定roomId、日期和时段", booking_tool.description)
        self.assertIn("不执行真实预约", booking_tool.description)

    def test_floor_is_required_for_search_tool(self):
        search_tool, _ = create_meeting_room_tools(self.client)

        with self.assertRaises(ValidationError):
            search_tool.invoke({})
        with self.assertRaises(ValidationError):
            search_tool.invoke({"floor": 0})
        self.assertEqual(self.gateway.select_calls, [])

    def test_search_rejects_explicit_blank_room_and_equipment(self):
        search_tool, _ = create_meeting_room_tools(self.client)

        invalid_arguments = (
            {"floor": 7, "roomQuery": "   "},
            {"floor": 7, "requirements": {"equipment": [" "]}},
            {
                "floor": 7,
                "requirements": {"equipment": ["视频会议", ""]},
            },
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValidationError):
                    search_tool.invoke(arguments)

        self.assertEqual(self.gateway.select_calls, [])

    def test_search_without_time_returns_only_static_room_fields(self):
        search_tool, _ = create_meeting_room_tools(self.client)
        result = json.loads(search_tool.invoke({"floor": 7}))

        self.assertTrue(result["success"])
        self.assertEqual(self.gateway.select_calls[-1]["floor"], "7")
        self.assertEqual(
            set(result["rooms"][0]),
            {"roomId", "roomName", "floor", "capacity", "equipment"},
        )
        self.assertNotIn("timeline", result["rooms"][0])
        self.assertNotIn("time", result)
        self.assertNotIn("roomQuery", result)
        self.assertNotIn("requirements", result)

    def test_search_schema_rejects_unknown_arguments(self):
        search_tool, _ = create_meeting_room_tools(self.client)

        with self.assertRaises(ValidationError):
            search_tool.invoke({"floor": 7, "type": "all"})
        with self.assertRaises(ValidationError):
            search_tool.invoke(
                {
                    "floor": 7,
                    "requirements": {"unknownConstraint": True},
                }
            )
        self.assertEqual(self.gateway.select_calls, [])

    def test_search_filters_room_name_inside_one_floor_call(self):
        search_tool, _ = create_meeting_room_tools(self.client)

        matched = json.loads(
            search_tool.invoke({"floor": 7, "roomQuery": " 707 "})
        )
        missing = json.loads(
            search_tool.invoke({"floor": 7, "roomQuery": "708"})
        )

        self.assertEqual(matched["matchedRoomCount"], 1)
        self.assertEqual(matched["rooms"][0]["roomId"], "165")
        self.assertEqual(missing["matchedRoomCount"], 0)
        self.assertEqual(missing["emptyReason"], "room_not_found_on_floor")
        self.assertTrue(
            all(call["room_query"] is None for call in self.gateway.select_calls)
        )

    def test_zero_results_identify_the_first_unsatisfied_constraint(self):
        search_tool, _ = create_meeting_room_tools(self.client)

        cases = (
            ({"floor": 8}, "floor_has_no_rooms"),
            (
                {"floor": 7, "requirements": {"minCapacity": 31}},
                "no_room_meets_capacity",
            ),
            (
                {"floor": 7, "requirements": {"equipment": ["白板"]}},
                "no_room_has_required_equipment",
            ),
        )
        for arguments, expected_reason in cases:
            with self.subTest(arguments=arguments):
                result = json.loads(search_tool.invoke(arguments))
                self.assertEqual(result["matchedRoomCount"], 0)
                self.assertEqual(result["emptyReason"], expected_reason)

    def test_search_composes_static_and_time_filters_in_one_call(self):
        search_tool, _ = create_meeting_room_tools(self.client)
        result = json.loads(
            search_tool.invoke(
                {
                    "floor": 7,
                    "time": {
                        "date": "2026/07/29",
                        "start": "14:00",
                        "end": "15:00",
                    },
                    "requirements": {
                        "minCapacity": 10,
                        "equipment": ["视频会议"],
                        "availableOnly": True,
                    },
                }
            )
        )

        self.assertEqual(len(self.gateway.select_calls), 1)
        self.assertEqual(result["matchedRoomCount"], 1)
        self.assertEqual(result["rooms"][0]["capacity"], 30)
        self.assertEqual(result["rooms"][0]["equipment"], ["视频会议"])
        self.assertTrue(result["rooms"][0]["isAvailable"])

    def test_named_room_is_preserved_when_requested_window_is_occupied(self):
        self.gateway.available = False
        search_tool, _ = create_meeting_room_tools(self.client)
        query = {
            "floor": 7,
            "roomQuery": "707",
            "time": {
                "date": "2026/07/29",
                "start": "14:00",
                "end": "15:00",
            },
        }

        inspected = json.loads(search_tool.invoke(query))
        available_only = json.loads(
            search_tool.invoke(
                {
                    **query,
                    "requirements": {"availableOnly": True},
                }
            )
        )

        self.assertEqual(inspected["matchedRoomCount"], 1)
        self.assertFalse(inspected["rooms"][0]["isAvailable"])
        self.assertEqual(
            inspected["rooms"][0]["conflicts"],
            [{"occupiedBy": "李小明", "timeRange": "14:00-15:00"}],
        )
        self.assertEqual(available_only["matchedRoomCount"], 0)
        self.assertEqual(
            available_only["emptyReason"],
            "no_room_available_in_time",
        )

    def test_available_only_requires_an_exact_time_window(self):
        search_tool, _ = create_meeting_room_tools(self.client)

        with self.assertRaises(ValidationError):
            search_tool.invoke(
                {
                    "floor": 7,
                    "requirements": {"availableOnly": True},
                }
            )

    def test_search_time_window_must_stay_inside_business_hours(self):
        search_tool, _ = create_meeting_room_tools(self.client)

        with self.assertRaises(ValidationError):
            search_tool.invoke(
                {
                    "floor": 7,
                    "time": {"start": "08:30", "end": "09:30"},
                }
            )
        with self.assertRaises(ValidationError):
            search_tool.invoke(
                {
                    "floor": 7,
                    "time": {"start": "18:00", "end": "19:00"},
                }
            )
        self.assertEqual(self.gateway.select_calls, [])

    def test_search_with_time_returns_thin_time_view_and_filters_locally(self):
        search_tool, _ = create_meeting_room_tools(self.client)
        result = json.loads(
            search_tool.invoke(
                {
                    "floor": 7,
                    "time": {
                        "date": "2026/07/29",
                        "start": "14:00",
                        "end": "15:00",
                    },
                }
            )
        )

        self.assertTrue(result["success"])
        self.assertEqual(self.gateway.select_calls[-1]["floor"], "7")
        self.assertIsNone(self.gateway.select_calls[-1]["time_range"])
        self.assertEqual(self.gateway.select_calls[-1]["date"], "2026/07/29")
        self.assertEqual(result["time"]["date"], "2026/07/29")
        room = result["rooms"][0]
        self.assertEqual(
            set(room),
            {
                "roomId",
                "roomName",
                "floor",
                "timeline",
                "availableTimeRanges",
                "isAvailable",
                "conflicts",
            },
        )
        self.assertNotIn("capacity", room)
        self.assertNotIn("equipment", room)
        self.assertEqual(
            [slot["timeRange"] for slot in room["timeline"]],
            ["14:00-14:30", "14:30-15:00"],
        )
        self.assertEqual(room["availableTimeRanges"], ["14:00-15:00"])

    def test_search_with_empty_time_returns_full_day_view(self):
        search_tool, _ = create_meeting_room_tools(self.client)
        result = json.loads(search_tool.invoke({"floor": 7, "time": {}}))

        self.assertEqual(result["time"]["date"], "2026/07/28")
        self.assertEqual(len(result["rooms"][0]["timeline"]), 3)

    def test_search_accepts_a_modifiable_date_without_time_window(self):
        search_tool, _ = create_meeting_room_tools(self.client)
        result = json.loads(
            search_tool.invoke(
                {"floor": 7, "time": {"date": "2026/07/30"}}
            )
        )

        self.assertEqual(self.gateway.select_calls[-1]["date"], "2026/07/30")
        self.assertEqual(result["date"], "2026/07/30")
        self.assertEqual(result["time"]["date"], "2026/07/30")
        self.assertIsNone(result["time"]["start"])
        self.assertIsNone(result["time"]["end"])
        self.assertEqual(len(result["rooms"][0]["timeline"]), 3)

    def test_booking_tool_creates_only_local_confirmation_draft(self):
        _, booking_tool = create_meeting_room_tools(self.client)
        result = json.loads(
            booking_tool.invoke(
                {
                    "roomId": "165",
                    "time": {
                        "date": "2026/07/29",
                        "start": "14:00",
                        "end": "15:00",
                    },
                }
            )
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["type"], "meetingRoomBookingDraft")
        self.assertEqual(result["roomName"], "707会议室")
        self.assertEqual(result["floor"], "7")
        self.assertIsNone(self.gateway.select_calls[0]["floor"])
        self.assertEqual(self.gateway.select_calls[0]["room_query"], "165")
        self.assertEqual(self.gateway.select_calls[1]["floor"], "7")
        self.assertEqual(self.gateway.select_calls[1]["time_range"], "14:00-15:00")
        self.assertEqual(self.gateway.select_calls[-1]["floor"], "7")
        self.assertEqual(self.gateway.create_calls, [])

    def test_booking_tool_defaults_capacity_without_asking(self):
        _, booking_tool = create_meeting_room_tools(self.client)

        self.assertEqual(
            set(booking_tool.args_schema.model_fields),
            {"roomId", "time", "capacity"},
        )
        validated = booking_tool.args_schema(
            roomId="165",
            time={"start": "14:00", "end": "15:00"},
        )
        self.assertIsNone(validated.time.date)
        self.assertEqual(validated.capacity, 5)

        result = json.loads(
            booking_tool.invoke(
                {
                    "roomId": "165",
                    "time": {"start": "14:00", "end": "15:00"},
                }
            )
        )

        self.assertEqual(result["date"], "2026/07/28")
        self.assertEqual(result["capacity"], 5)
        self.assertEqual(result["bookedBy"], "程少伟")
        self.assertEqual(result["theme"], "程少伟预定的会议")

    def test_booking_tool_accepts_user_supplied_capacity(self):
        _, booking_tool = create_meeting_room_tools(self.client)

        result = json.loads(
            booking_tool.invoke(
                {
                    "roomId": "165",
                    "time": {
                        "date": "2026/07/29",
                        "start": "14:00",
                        "end": "15:00",
                    },
                    "capacity": 10,
                }
            )
        )

        self.assertEqual(result["capacity"], 10)
        self.assertEqual(self.gateway.select_calls[-1]["capacity"], 10)

    def test_booking_card_requires_exact_half_hour_boundaries(self):
        _, booking_tool = create_meeting_room_tools(self.client)

        with self.assertRaises(ValidationError):
            booking_tool.invoke(
                {
                    "roomId": "165",
                    "time": {"start": "14:10", "end": "15:10"},
                }
            )

    def test_booking_conflict_returns_occupant_and_time_range(self):
        self.gateway.available = False
        _, booking_tool = create_meeting_room_tools(self.client)

        result = json.loads(
            booking_tool.invoke(
                {
                    "roomId": "165",
                    "time": {
                        "date": "2026/07/29",
                        "start": "14:00",
                        "end": "15:00",
                    },
                }
            )
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["conflict"])
        self.assertEqual(result["occupiedBy"], "李小明")
        self.assertEqual(result["occupiedTimeRange"], "14:00-15:00")

    def test_booking_theme_default_tracks_current_authenticated_user(self):
        current_user = ["程少伟"]
        store = MeetingRoomDraftStore(
            self.gateway,
            Path(self.temp_dir.name) / "dynamic-user.db",
            now_factory=lambda: FIXED_NOW,
            booked_by_provider=lambda: current_user[0],
        )
        _, booking_tool = create_meeting_room_tools(
            MeetingRoomAgentClient(self.gateway, store)
        )
        base = {
            "roomId": "165",
            "time": {
                "date": "2026/07/29",
                "start": "14:00",
                "end": "15:00",
            },
        }

        first = json.loads(booking_tool.invoke(base))
        current_user[0] = "李小明"
        second = json.loads(booking_tool.invoke(base))

        self.assertEqual(first["theme"], "程少伟预定的会议")
        self.assertEqual(second["theme"], "李小明预定的会议")

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
        closing_slot = self.store.create_draft(
            room_id="165",
            floor="7",
            date="2026/07/29",
            time_range="18:00-18:30",
            capacity=5,
            theme=None,
        )
        self.assertEqual(closing_slot["timeRange"], "18:00-18:30")

        invalid = (
            ("2026/07/28", "13:00-13:30", "过去"),
            ("2026/07/29", "08:30-09:30", "09:00-18:30"),
            ("2026/07/29", "18:30-19:00", "09:00-18:30"),
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

    def test_today_booking_start_rounds_right_to_half_hour(self):
        exact_boundary_store = MeetingRoomDraftStore(
            self.gateway,
            Path(self.temp_dir.name) / "exact-boundary.db",
            now_factory=lambda: datetime.fromisoformat(
                "2026-07-28T11:30:00+08:00"
            ),
        )
        after_boundary_store = MeetingRoomDraftStore(
            self.gateway,
            Path(self.temp_dir.name) / "after-boundary.db",
            now_factory=lambda: datetime.fromisoformat(
                "2026-07-28T11:31:00+08:00"
            ),
        )

        exact = exact_boundary_store.create_draft(
            room_id="165",
            floor="7",
            date="2026/07/28",
            time_range="11:30-12:30",
            capacity=5,
            theme=None,
        )
        self.assertEqual(exact["timeRange"], "11:30-12:30")

        with self.assertRaisesRegex(ValueError, "不能预约过去的时间"):
            after_boundary_store.create_draft(
                room_id="165",
                floor="7",
                date="2026/07/28",
                time_range="11:30-12:30",
                capacity=5,
                theme=None,
            )
        rounded = after_boundary_store.create_draft(
            room_id="165",
            floor="7",
            date="2026/07/28",
            time_range="12:00-13:00",
            capacity=5,
            theme=None,
        )
        self.assertEqual(rounded["timeRange"], "12:00-13:00")


if __name__ == "__main__":
    unittest.main()
