from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

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
            store = PeopleStore(db_path)
            for person in SANDBOX_PEOPLE:
                self.assertEqual(
                    store.find(employee_id=person["employee_id"])["status"],
                    "found",
                )


if __name__ == "__main__":
    unittest.main()
