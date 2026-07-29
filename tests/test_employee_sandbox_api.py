from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import server
from people_tool import PeopleStore


class EmployeeSandboxApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_mode = server.SANDBOX_MODE
        self.original_store = server.people_store
        self.original_frontend_index = server.FRONTEND_INDEX_PATH
        server.SANDBOX_MODE = True
        server.FRONTEND_INDEX_PATH = (
            server.PROJECT_DIR / "frontend" / "index.html"
        )
        server.people_store = PeopleStore(
            Path(self.temp_dir.name) / "employee-sandbox.db"
        )
        self.client = TestClient(server.app)

    def tearDown(self):
        self.client.close()
        server.SANDBOX_MODE = self.original_mode
        server.people_store = self.original_store
        server.FRONTEND_INDEX_PATH = self.original_frontend_index
        self.temp_dir.cleanup()

    def test_page_and_status_are_only_available_in_sandbox_mode(self):
        self.assertEqual(self.client.get("/employee-sandbox").status_code, 200)
        status = self.client.get("/api/sandbox/status")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["sandbox"])

        server.SANDBOX_MODE = False
        self.assertEqual(self.client.get("/employee-sandbox").status_code, 404)
        self.assertEqual(self.client.get("/api/sandbox/people").status_code, 404)

    def test_create_search_update_and_delete_employee(self):
        created = self.client.post(
            "/api/sandbox/people",
            json={
                "employee_id": "XY-T001",
                "name": "测试员工",
                "phone": "13900000001",
                "department": "测试部",
            },
        )
        self.assertEqual(created.status_code, 201)

        searched = self.client.get(
            "/api/sandbox/people",
            params={"search": "测试"},
        )
        self.assertEqual(searched.status_code, 200)
        self.assertEqual(len(searched.json()), 1)

        updated = self.client.put(
            "/api/sandbox/people/XY-T001",
            json={
                "employee_id": "XY-T002",
                "name": "测试员工二",
                "phone": "13900000002",
                "department": "质量部",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["employee_id"], "XY-T002")

        deleted = self.client.delete("/api/sandbox/people/XY-T002")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get("/api/sandbox/people").json(), [])

    def test_duplicate_employee_id_or_phone_returns_conflict(self):
        payload = {
            "employee_id": "XY-T001",
            "name": "测试员工",
            "phone": "13900000001",
            "department": "测试部",
        }
        self.assertEqual(
            self.client.post("/api/sandbox/people", json=payload).status_code,
            201,
        )

        conflict = self.client.post(
            "/api/sandbox/people",
            json={**payload, "name": "另一位员工"},
        )

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"], "工号或手机号已存在")


if __name__ == "__main__":
    unittest.main()
