from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

import server
from meeting_room_tool import MeetingRoomStore


FIXED_NOW = datetime.fromisoformat("2026-07-28T13:30:00+08:00")


class MeetingRoomSandboxApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_mode = server.SANDBOX_MODE
        self.original_store = server.meeting_room_store
        server.SANDBOX_MODE = True
        server.meeting_room_store = MeetingRoomStore(
            Path(self.temp_dir.name) / "meeting-room-sandbox.db",
            now_factory=lambda: FIXED_NOW,
        )
        self.client = TestClient(server.app)

    def tearDown(self):
        self.client.close()
        server.SANDBOX_MODE = self.original_mode
        server.meeting_room_store = self.original_store
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


if __name__ == "__main__":
    unittest.main()
