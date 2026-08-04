from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app import main as server
from app.features.meeting_room.draft_store import MeetingRoomDraftStore


class FakeExternalGateway:
    def __init__(self):
        self.create_calls = 0

    def select_meet(self, **kwargs):
        room_id = kwargs.get("room_query") or "165"
        return {
            "success": True,
            "rooms": [
                {
                    "roomId": room_id,
                    "roomName": "707会议室",
                    "floor": "7F",
                    "capacity": 30,
                    "available": True,
                }
            ],
        }

    def create(self, **kwargs):
        self.create_calls += 1
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
        }


class MeetingRoomDraftApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.gateway = FakeExternalGateway()
        self.store = MeetingRoomDraftStore(
            self.gateway,
            Path(self.temp_dir.name) / "draft-api.db",
            now_factory=lambda: datetime(2026, 8, 4, 10, 0),
        )
        self.original_store = server.meeting_room_drafts
        server.meeting_room_drafts = self.store
        self.client = TestClient(server.app)

    def tearDown(self):
        self.client.close()
        server.meeting_room_drafts = self.original_store
        self.temp_dir.cleanup()

    def test_removed_local_business_endpoints_return_not_found(self):
        paths = (
            "/api/sandbox/status",
            "/api/sandbox/people",
            "/api/sandbox/meeting-rooms",
            "/employee-sandbox",
            "/meeting-room-sandbox",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_card_confirmation_is_the_only_external_write(self):
        draft = self.store.create_draft(
            room_id="165",
            floor="7",
            date="2026/08/05",
            time_range="14:00-15:00",
            capacity=5,
            theme="项目联调",
            session_id="session-1",
            round_no=1,
        )
        self.assertEqual(self.gateway.create_calls, 0)
        fetched = self.client.get(
            f"/api/meeting-room-booking-drafts/{draft['draftId']}"
        )
        self.assertEqual(fetched.status_code, 200)

        confirmed = self.client.post(
            f"/api/meeting-room-booking-drafts/{draft['draftId']}/confirm"
        )

        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(self.gateway.create_calls, 1)
        self.assertEqual(confirmed.json()["draft"]["status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
