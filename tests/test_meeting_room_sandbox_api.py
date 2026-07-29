from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
from conversation_store import ConversationStore
from meeting_room_tool import MeetingRoomStore


FIXED_NOW = datetime.fromisoformat("2026-07-28T13:30:00+08:00")


class MeetingRoomSandboxApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_mode = server.SANDBOX_MODE
        self.original_store = server.meeting_room_store
        self.original_conversation_store = server.conversation_store
        server.SANDBOX_MODE = True
        server.meeting_room_store = MeetingRoomStore(
            Path(self.temp_dir.name) / "meeting-room-sandbox.db",
            now_factory=lambda: FIXED_NOW,
        )
        server.conversation_store = ConversationStore(
            Path(self.temp_dir.name) / "conversations.db"
        )
        self.client = TestClient(server.app)

    def tearDown(self):
        self.client.close()
        server.SANDBOX_MODE = self.original_mode
        server.meeting_room_store = self.original_store
        server.conversation_store = self.original_conversation_store
        self.temp_dir.cleanup()

    def test_page_and_api_are_only_available_in_sandbox_mode(self):
        self.assertEqual(
            self.client.get("/meeting-room-sandbox").status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                "/api/sandbox/meeting-rooms",
                params={"floor": "7"},
            ).status_code,
            200,
        )

        server.SANDBOX_MODE = False
        self.assertEqual(
            self.client.get("/meeting-room-sandbox").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                "/api/sandbox/meeting-rooms",
                params={"floor": "7"},
            ).status_code,
            404,
        )

    def test_status_lists_both_sandbox_destinations(self):
        status = self.client.get("/api/sandbox/status")

        self.assertEqual(status.status_code, 200)
        self.assertEqual(
            [item["id"] for item in status.json()["destinations"]],
            ["employees", "meeting-rooms"],
        )

    def test_date_schedule_returns_every_floor_and_half_hour_slots(self):
        response = self.client.get(
            "/api/sandbox/meeting-rooms",
            params={"date": "2026/07/28"},
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(len(result["rooms"]), 9)
        self.assertEqual(
            {room["floor"] for room in result["rooms"]},
            {"6F", "7F", "8F"},
        )
        self.assertTrue(
            all(len(room["timeline"]) == 9 for room in result["rooms"])
        )
        self.assertEqual(result["displayWindow"], "13:30-18:00")
        room_707 = next(
            room for room in result["rooms"]
            if room["roomId"] == "room-707"
        )
        self.assertEqual(
            [
                slot["timeRange"]
                for slot in room_707["timeline"]
                if not slot["available"]
            ],
            [],
        )

    def test_meeting_room_page_is_read_only_schedule_browser(self):
        page = self.client.get("/meeting-room-sandbox")

        self.assertIn("只读沙箱", page.text)
        self.assertIn("09:00–18:00", page.text)
        self.assertNotIn("确认并写入预约", page.text)

    def test_booking_persists_and_is_visible_in_next_room_query(self):
        created = self.client.post(
            "/api/sandbox/meeting-room-bookings",
            json={
                "roomId": "room-707",
                "floor": "7",
                "date": "2026/07/29",
                "timeRange": "09:00-10:00",
                "confirmed": True,
                "capacity": 5,
                "theme": "接口联调",
            },
        )

        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.json()["bookingId"])
        rooms = self.client.get(
            "/api/sandbox/meeting-rooms",
            params={
                "floor": "7",
                "date": "2026/07/29",
                "timeRange": "09:00-10:00",
                "capacity": 5,
            },
        ).json()["rooms"]
        room = next(item for item in rooms if item["roomId"] == "room-707")
        self.assertFalse(room["available"])
        self.assertEqual(room["occupied"][0]["theme"], "接口联调")

    def test_conflict_returns_409_and_false_confirmation_returns_422(self):
        payload = {
            "roomId": "room-707",
            "floor": "7",
            "date": "2026/07/29",
            "timeRange": "09:00-10:00",
            "confirmed": True,
            "capacity": 5,
            "theme": "第一场",
        }
        self.assertEqual(
            self.client.post(
                "/api/sandbox/meeting-room-bookings",
                json=payload,
            ).status_code,
            201,
        )
        self.assertEqual(
            self.client.post(
                "/api/sandbox/meeting-room-bookings",
                json={**payload, "theme": "冲突场次"},
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                "/api/sandbox/meeting-room-bookings",
                json={**payload, "confirmed": False},
            ).status_code,
            422,
        )

    def test_booking_card_can_edit_then_human_confirm(self):
        draft = server.meeting_room_store.create_draft(
            room_id="room-711",
            floor="7",
            date="2026/07/29",
            time_range="15:00-16:00",
            session_id="card-session",
            round_no=2,
        )
        self.assertIsNone(draft["bookingId"])

        updated = self.client.put(
            f"/api/meeting-room-booking-drafts/{draft['draftId']}",
            json={
                "roomId": "room-711",
                "floor": "7",
                "date": "2026/07/29",
                "timeRange": "16:00-17:00",
                "capacity": 6,
                "theme": "卡片修改后的主题",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["timeRange"], "16:00-17:00")

        confirmed = self.client.post(
            f"/api/meeting-room-booking-drafts/{draft['draftId']}/confirm"
        )
        self.assertEqual(confirmed.status_code, 200)
        result = confirmed.json()
        self.assertTrue(result["bookingId"])
        self.assertTrue(result["meetingId"])
        self.assertEqual(result["draft"]["status"], "confirmed")

    def test_session_context_restores_booking_card_by_round(self):
        server.meeting_room_store.create_draft(
            room_id="room-711",
            floor="7",
            date="2026/07/29",
            time_range="16:00-17:00",
            session_id="restored-card",
            round_no=1,
        )
        server.conversation_store.append_round(
            "restored-card",
            "帮我约会议室",
            "请在卡片中确认。",
        )

        fake_context = {
            "title": "会议室预约",
            "summary": "",
            "summary_range": None,
            "rounds": 1,
            "uncovered_rounds": 1,
            "compression_pending": False,
            "compression_error": None,
            "messages": server.conversation_store.get_messages(
                "restored-card"
            ),
        }
        with patch.object(server, "get_context", return_value=fake_context):
            context = self.client.get("/api/sessions/restored-card")

        self.assertEqual(context.status_code, 200)
        self.assertEqual(
            context.json()["artifactsByRound"]["1"][0]["status"],
            "pending",
        )

    def test_chat_page_contains_editable_human_confirmation_card(self):
        page = self.client.get("/")

        self.assertIn("booking-card", page.text)
        self.assertIn("保存修改", page.text)
        self.assertIn("确认预约", page.text)
        self.assertIn("/confirm", page.text)
        self.assertIn("artifact-only", page.text)
        self.assertIn("actions.hidden = true", page.text)
        self.assertIn("is-confirmed", page.text)


if __name__ == "__main__":
    unittest.main()
