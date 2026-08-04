from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as server
from app.features.current_user.service import CurrentUser, CurrentUserNotFoundError


class FakeCurrentUserService:
    def __init__(self):
        self.user = CurrentUser("160218", "程少伟")
        self.resolve_calls: list[str] = []
        self.switch_calls: list[str] = []

    def current(self) -> CurrentUser:
        return self.user

    def resolve(self, employee_id: str) -> CurrentUser:
        self.resolve_calls.append(employee_id)
        if employee_id == "999999":
            raise CurrentUserNotFoundError("通讯录中没有工号 999999，不能切换用户")
        return CurrentUser(employee_id, "张三")

    def switch(self, employee_id: str) -> CurrentUser:
        self.switch_calls.append(employee_id)
        self.user = self.resolve(employee_id)
        return self.user


class CurrentUserApiTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeCurrentUserService()
        self.client = TestClient(server.app)

    def test_resolve_does_not_switch_then_put_switches(self):
        with patch.object(server, "current_user_service", self.service):
            current = self.client.get("/api/current-user")
            resolved = self.client.post(
                "/api/current-user/resolve",
                json={"employeeId": "100001"},
            )
            unchanged = self.client.get("/api/current-user")
            switched = self.client.put(
                "/api/current-user",
                json={"employeeId": "100001"},
            )

        self.assertEqual(current.json(), {"employeeId": "160218", "name": "程少伟"})
        self.assertEqual(resolved.json(), {"employeeId": "100001", "name": "张三"})
        self.assertEqual(unchanged.json(), current.json())
        self.assertEqual(switched.json(), resolved.json())
        self.assertEqual(self.service.switch_calls, ["100001"])

    def test_unknown_or_invalid_employee_id_is_rejected(self):
        with patch.object(server, "current_user_service", self.service):
            unknown = self.client.put(
                "/api/current-user",
                json={"employeeId": "999999"},
            )
            invalid = self.client.put(
                "/api/current-user",
                json={"employeeId": "100 001"},
            )

        self.assertEqual(unknown.status_code, 404)
        self.assertIn("不能切换用户", unknown.json()["detail"])
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.service.user.name, "程少伟")


if __name__ == "__main__":
    unittest.main()
