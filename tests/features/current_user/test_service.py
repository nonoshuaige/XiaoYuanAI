from __future__ import annotations

import unittest

from app.features.current_user.service import (
    CurrentUserNotFoundError,
    CurrentUserService,
)
from app.integrations.mock_sandbox.client import (
    MockSandboxHttpClient,
    MockSandboxSettings,
)


class FakeDirectory:
    def __init__(self, results: dict[str, dict]):
        self.results = results
        self.calls: list[dict] = []

    def find(self, **kwargs):
        self.calls.append(kwargs)
        return self.results[kwargs["employee_id"]]


def found(employee_id: str, name: str) -> dict:
    return {
        "status": "found",
        "people": [
            {
                "employee_id": employee_id,
                "name": name,
                "phone": "",
                "department": "数字化部",
            }
        ],
    }


class CurrentUserServiceTests(unittest.TestCase):
    def setUp(self):
        self.http = MockSandboxHttpClient(
            MockSandboxSettings(
                base_url="http://127.0.0.1:18080",
                timeout_seconds=5,
                user_id="160218",
                user_name="程少伟",
            )
        )

    def tearDown(self):
        self.http._client.close()

    def test_resolve_gets_name_from_person_directory_without_switching(self):
        directory = FakeDirectory({"100001": found("100001", "张三")})
        service = CurrentUserService(self.http, directory)

        resolved = service.resolve(" 100001 ")

        self.assertEqual(resolved.as_dict(), {"employeeId": "100001", "name": "张三"})
        self.assertEqual(directory.calls, [{"employee_id": "100001"}])
        self.assertEqual(service.current().name, "程少伟")

    def test_switch_uses_directory_name_and_updates_http_identity(self):
        directory = FakeDirectory({"100001": found("100001", "张三")})
        service = CurrentUserService(self.http, directory)

        switched = service.switch("100001")

        self.assertEqual(switched.name, "张三")
        self.assertEqual(self.http.settings.user_id, "100001")
        self.assertEqual(self.http.settings.user_name, "张三")

    def test_unknown_employee_cannot_replace_current_user(self):
        directory = FakeDirectory(
            {"999999": {"status": "not_found", "people": []}}
        )
        service = CurrentUserService(self.http, directory)

        with self.assertRaisesRegex(CurrentUserNotFoundError, "不能切换用户"):
            service.switch("999999")

        self.assertEqual(service.current().employee_id, "160218")
        self.assertEqual(service.current().name, "程少伟")


if __name__ == "__main__":
    unittest.main()
