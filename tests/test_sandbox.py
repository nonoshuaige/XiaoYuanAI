from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from conversation_store import ConversationStore
from people_tool import PeopleStore
from sandbox import (
    LEGACY_MIGRATION_KEY,
    SANDBOX_PEOPLE,
    migrate_legacy_sandbox_database,
    seed_sandbox_people,
)


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

    def test_seed_only_cli_uses_configured_shared_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "xiaoyuan.db"
            environment = os.environ.copy()
            environment["XIAOYUAN_DB_PATH"] = str(db_path)

            completed = subprocess.run(
                [
                    str(PYTHON),
                    "sandbox.py",
                    "--seed-only",
                ],
                cwd=PROJECT_DIR,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertTrue(db_path.exists())
            self.assertIn("已写入并验证 10 条", completed.stdout)
            store = PeopleStore(db_path)
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
                ],
                cwd=PROJECT_DIR,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn("已保留统一数据库中的 10 条", repeated.stdout)
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
                ],
                cwd=PROJECT_DIR,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("已保留统一数据库中的 0 条", empty_restart.stdout)
            self.assertEqual(store.list_all(), [])

    def test_legacy_database_is_merged_once_into_shared_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            target_path = temp_path / "xiaoyuan.db"
            source_path = temp_path / "sandbox.db"
            target_store = ConversationStore(target_path)
            source_store = ConversationStore(source_path)
            target_people = PeopleStore(target_path)
            source_people = PeopleStore(source_path)

            target_store.begin_round("normal-session", "普通模式消息")
            target_store.complete_round(
                "normal-session",
                1,
                "普通模式回复",
                model_call={
                    "model_id": "normal-model",
                    "provider_responses": [],
                },
            )
            source_store.begin_round("sandbox-session", "沙箱模式消息")
            source_store.complete_round(
                "sandbox-session",
                1,
                "沙箱模式回复",
                model_call={
                    "model_id": "sandbox-model",
                    "provider_responses": [],
                },
            )
            source_people.create(
                employee_id="XY-M001",
                name="迁移员工",
                phone="13900000001",
                department="迁移测试部",
            )

            migrated = migrate_legacy_sandbox_database(
                target_path,
                source_path,
            )

            self.assertEqual(migrated["sessions"], 1)
            self.assertEqual(migrated["chat_messages"], 2)
            self.assertEqual(migrated["people"], 1)
            self.assertEqual(
                target_store.get_session("sandbox-session")["rounds"],
                1,
            )
            self.assertEqual(
                target_people.find(employee_id="XY-M001")["status"],
                "found",
            )
            self.assertEqual(
                migrate_legacy_sandbox_database(target_path, source_path),
                {},
            )
            with sqlite3.connect(target_path) as connection:
                marker = connection.execute(
                    "SELECT value FROM app_metadata WHERE key = ?",
                    (LEGACY_MIGRATION_KEY,),
                ).fetchone()
            self.assertEqual(marker, (str(source_path.resolve()),))


if __name__ == "__main__":
    unittest.main()
