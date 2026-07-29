from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from meeting_room_tool import MeetingRoomStore
from people_tool import PeopleStore
from sandbox import SANDBOX_PEOPLE, seed_sandbox_people


PROJECT_DIR = Path(__file__).resolve().parents[1]
PYTHON = Path("/Users/zypro/Desktop/pythonenv/envs/XiaoYuan/bin/python")


class SandboxTests(unittest.TestCase):
    def test_seed_is_idempotent_and_keeps_expected_duplicate_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = PeopleStore(Path(temp_dir) / "sandbox.db")

            self.assertEqual(seed_sandbox_people(store), 10)
            self.assertEqual(seed_sandbox_people(store), 10)
            result = store.find(name="张三")

            self.assertEqual(result["status"], "multiple_matches")
            self.assertEqual(len(result["people"]), 2)

    def test_seed_only_cli_uses_isolated_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "isolated-sandbox.db"
            environment = os.environ.copy()
            environment.pop("XIAOYUAN_DB_PATH", None)

            completed = subprocess.run(
                [
                    str(PYTHON),
                    "sandbox.py",
                    "--seed-only",
                    "--db",
                    str(db_path),
                ],
                cwd=PROJECT_DIR,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertTrue(db_path.exists())
            self.assertIn("已写入并验证 10 条", completed.stdout)
            self.assertIn("已验证 9 间虚构会议室", completed.stdout)
            store = PeopleStore(db_path)
            meeting_store = MeetingRoomStore(db_path)
            self.assertEqual(
                sum(
                    len(meeting_store.list_rooms(floor=floor)["rooms"])
                    for floor in ("6", "7", "8")
                ),
                9,
            )
            for person in SANDBOX_PEOPLE:
                self.assertEqual(
                    store.find(employee_id=person["employee_id"])["status"],
                    "found",
                )

            store.update(
                "XY-S001",
                employee_id="XY-S001",
                name="用户修改后姓名",
                phone="13800000001",
                department="用户修改后部门",
            )
            repeated = subprocess.run(
                [
                    str(PYTHON),
                    "sandbox.py",
                    "--seed-only",
                    "--db",
                    str(db_path),
                ],
                cwd=PROJECT_DIR,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn("已保留沙箱中的 10 条", repeated.stdout)
            preserved = store.find(employee_id="XY-S001")["people"][0]
            self.assertEqual(preserved["name"], "用户修改后姓名")
            self.assertEqual(preserved["department"], "用户修改后部门")

            for person in store.list_all():
                store.delete(person["employee_id"])
            empty_restart = subprocess.run(
                [
                    str(PYTHON),
                    "sandbox.py",
                    "--seed-only",
                    "--db",
                    str(db_path),
                ],
                cwd=PROJECT_DIR,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("已保留沙箱中的 0 条", empty_restart.stdout)
            self.assertEqual(store.list_all(), [])


if __name__ == "__main__":
    unittest.main()
