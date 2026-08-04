from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.features.leave.skill import create_leave_skill
from app.features.leave.tools import MockSandboxLeaveClient
from app.features.meeting_room.draft_store import MeetingRoomDraftStore
from app.features.meeting_room.gateway import MockSandboxMeetingRoomGateway
from app.integrations.mock_sandbox.client import MockSandboxSettings
from app.features.people.tools import MockSandboxPeopleClient


class FakeSandboxHttp:
    def __init__(self):
        self.settings = MockSandboxSettings(
            base_url="http://127.0.0.1:18080",
            timeout_seconds=5,
            user_id="160218",
            user_name="程少伟",
        )
        self.calls: list[dict] = []

    def request_json(self, method, path, **kwargs):
        self.calls.append({"method": method, "path": path, **kwargs})
        if path.endswith("/addressbook/search"):
            return {
                "code": 0,
                "data": {
                    "user": [
                        {
                            "loginCode": "160218",
                            "name": "程少伟",
                            "telPhone": "13800000008",
                            "orgName": "数字化部",
                        }
                    ]
                },
            }
        if path == "/oca/applet/login":
            return {"code": 0, "data": {"tokenId": "MOCK_TOKEN_ID"}}
        if path == "/oca/ibpmeetrese/q/selectMeet":
            return {
                "code": 0,
                "data": [
                    {
                        "roomId": "165",
                        "roomName": "707会议室",
                        "address": "7",
                        "capacity": "10;30",
                        "equip": "视频会议;白板",
                        "reservedInfoList": [
                            {
                                "reservedDate": "2026-08-07",
                                "startTime": "14:00",
                                "endTime": "15:00",
                                "theme": "项目联调",
                                "userName": "程少伟",
                                "userNo": "160218",
                            }
                        ],
                    }
                ],
            }
        if path == "/oca/ibpmeetrese/n/create":
            body = kwargs["json_body"]
            return {
                "code": 0,
                "data": {
                    "reserveId": "reserve-1",
                    "meetingId": "meeting-1",
                    "roomId": body["roomId"],
                    "roomName": "707会议室",
                    "reservedDate": body["reservedDate"],
                    "startTime": body["startTime"],
                    "endTime": body["endTime"],
                    "theme": body["theme"],
                },
            }
        if path.endswith("getTokenByParamThreeNew.do"):
            return {
                "result": "MOCK_LEAVE_TOKEN_8",
                "userId": "8",
                "success": True,
            }
        if path.endswith("getListDataForRestCode.do"):
            form = kwargs["form"]
            select_method = form["selectMethod"]
            if select_method == "getVacationsItem":
                result = [
                    {
                        "id": "mock-annual-leave-id",
                        "name": "年休假",
                        "code": "X15",
                    },
                    {
                        "id": "mock-personal-leave-id",
                        "name": "事假",
                        "code": "X01",
                    },
                ]
            elif select_method == "getTimeTemplate":
                result = [
                    {
                        "infoItems": [
                            {
                                "id": "Vacations",
                                "displayName": "剩余天数",
                                "value": "9",
                            }
                        ]
                    }
                ]
            elif select_method == "saveApplicationDTO":
                result = "request-1"
            elif select_method == "cancelApplicationDTO":
                result = "request-1"
            else:
                raise AssertionError(select_method)
            return {"success": "true", "reason": None, "result": result}
        raise AssertionError(f"unexpected sandbox path: {path}")


class MockSandboxToolTests(unittest.TestCase):
    def setUp(self):
        self.http = FakeSandboxHttp()

    def test_people_search_maps_compatibility_fields(self):
        result = MockSandboxPeopleClient(self.http).find(
            employee_id="160218",
            department="数字化部",
        )

        self.assertEqual(result["status"], "found")
        self.assertEqual(result["people"][0]["phone"], "13800000008")
        call = self.http.calls[0]
        self.assertEqual(call["params"]["searchValue"], "160218")
        self.assertEqual(call["headers"]["X-LOGINCODE"], "160218")

    def test_meeting_search_normalizes_schedule_and_capacity(self):
        gateway = MockSandboxMeetingRoomGateway(
            self.http,
            now_factory=lambda: datetime(2026, 8, 4, 10, 0),
        )

        result = gateway.select_meet(
            floor="7",
            date="2026/08/07",
            time_range="14:00-15:00",
            capacity=5,
        )

        room = result["rooms"][0]
        self.assertEqual(room["capacity"], 30)
        self.assertFalse(room["available"])
        self.assertEqual(room["occupied"][0]["theme"], "项目联调")
        select_call = self.http.calls[-1]
        self.assertEqual(select_call["json_body"], {"address": "7"})
        self.assertEqual(
            select_call["headers"]["Userauthorization"],
            "MOCK_TOKEN_ID",
        )

    def test_meeting_create_maps_confirmed_write_payload(self):
        gateway = MockSandboxMeetingRoomGateway(self.http)

        result = gateway.create(
            room_id="165",
            floor="7",
            date="2026/08/07",
            time_range="15:00-16:00",
            capacity=6,
            theme="项目联调",
        )

        self.assertEqual(result["bookingId"], "reserve-1")
        create_call = self.http.calls[-1]
        self.assertEqual(create_call["json_body"]["reservedDate"], "2026-08-07")
        self.assertEqual(create_call["json_body"]["capacity"], "6;6")
        self.assertEqual(create_call["json_body"]["reserveUserId"], "160218")

    def test_meeting_login_and_payload_follow_switched_user(self):
        gateway = MockSandboxMeetingRoomGateway(self.http)
        gateway.create(
            room_id="165",
            floor="7",
            date="2026/08/07",
            time_range="15:00-16:00",
            capacity=6,
            theme=None,
        )
        self.http.settings = MockSandboxSettings(
            base_url="http://127.0.0.1:18080",
            timeout_seconds=5,
            user_id="100001",
            user_name="张三",
        )

        gateway.create(
            room_id="165",
            floor="7",
            date="2026/08/07",
            time_range="16:00-17:00",
            capacity=6,
            theme=None,
        )

        login_calls = [
            call for call in self.http.calls if call["path"] == "/oca/applet/login"
        ]
        self.assertEqual(len(login_calls), 2)
        self.assertEqual(login_calls[-1]["json_body"]["loginCode"], "100001")
        create_call = self.http.calls[-1]
        self.assertEqual(create_call["json_body"]["reserveUserId"], "100001")
        self.assertEqual(create_call["json_body"]["reserveUserName"], "张三")
        self.assertEqual(create_call["json_body"]["theme"], "张三预约的会议")

    def test_leave_query_apply_and_cancel_use_rest_methods(self):
        client = MockSandboxLeaveClient(self.http)

        balance = client.query("160218")
        applied = client.apply(
            employee_id="160218",
            leave_type="年休假",
            start_date="2026/08/10",
            end_date="2026/08/10",
            period="上午",
            reason="家中有事",
        )
        cancelled = client.cancel(
            employee_id="160218",
            request_id=applied["requestId"],
        )

        self.assertEqual(balance["balances"][0]["remainingDays"], "9")
        self.assertEqual(cancelled["status"], "cancelled")
        rest_calls = [
            call
            for call in self.http.calls
            if call["path"].endswith("getListDataForRestCode.do")
        ]
        methods = [call["form"]["selectMethod"] for call in rest_calls]
        self.assertIn("saveApplicationDTO", methods)
        self.assertIn("cancelApplicationDTO", methods)
        save_call = next(
            call
            for call in rest_calls
            if call["form"]["selectMethod"] == "saveApplicationDTO"
        )
        save_param = json.loads(save_call["form"]["param"])
        duration = json.loads(
            save_param["dataObj"]["TB_TMG_APPLICATION_REPORT"]
            ["CUSTOM_DURATION"]["value"]
        )
        self.assertEqual(duration[0]["type"], "1")

    def test_leave_skill_registers_query_apply_and_cancel(self):
        skill = create_leave_skill(MockSandboxLeaveClient(self.http))
        self.assertEqual(
            skill.tool_names,
            ("queryLeaveBalance", "applyLeave", "cancelLeave"),
        )


class FakeMeetingGateway:
    def __init__(self):
        self.create_calls = 0

    def select_meet(self, **kwargs):
        return {
            "success": True,
            "rooms": [
                {
                    "roomId": "165",
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


class ExternalMeetingDraftTests(unittest.TestCase):
    def test_remote_create_only_runs_after_card_confirmation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            gateway = FakeMeetingGateway()
            store = MeetingRoomDraftStore(
                gateway,
                Path(temp_dir) / "meeting.db",
                now_factory=lambda: datetime(2026, 8, 4, 10, 0),
            )
            draft = store.create_draft(
                room_id="165",
                floor="7",
                date="2026/08/05",
                time_range="14:00-15:00",
                capacity=5,
                theme="项目联调",
                session_id="session-1",
                round_no=1,
            )

            self.assertEqual(draft["roomName"], "707会议室")
            self.assertEqual(gateway.create_calls, 0)
            confirmed = store.confirm_draft(draft["draftId"])
            self.assertEqual(gateway.create_calls, 1)
            self.assertEqual(confirmed["bookingId"], "reserve-1")
            self.assertEqual(confirmed["draft"]["status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
