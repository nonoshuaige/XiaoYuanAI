from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Sequence

from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from agent import AgentRuntime
from conversation_store import ConversationStore
from people_tool import (
    DuplicatePersonError,
    FindPersonInput,
    PeopleStore,
    PersonNotFoundError,
    create_find_person_tool,
)


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ):
        return self


class TrackingPeopleStore(PeopleStore):
    def __init__(self, db_path: Path):
        super().__init__(db_path)
        self.find_calls: list[dict[str, str | None]] = []

    def find(self, **kwargs):
        self.find_calls.append(kwargs)
        return super().find(**kwargs)


class PeopleStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = PeopleStore(Path(self.temp_dir.name) / "people.db")
        self.store.upsert(
            employee_id="E001",
            name="张三",
            phone="13800138001",
            department="研发部",
        )
        self.store.upsert(
            employee_id="E002",
            name="张三",
            phone="13800138002",
            department="财务部",
        )
        self.store.upsert(
            employee_id="E003",
            name="李四",
            phone="13800138003",
            department="研发部",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_requires_at_least_one_identity_clue(self):
        with self.assertRaisesRegex(ValidationError, "至少需要提供一个"):
            FindPersonInput(department="研发部")

    def test_conflicting_clues_do_not_return_a_person(self):
        result = self.store.find(
            employee_id="E001",
            phone="13800138003",
            name="李四",
        )

        self.assertEqual(result["status"], "conflicting_clues")
        self.assertEqual(result["matched_by"], "employee_id")
        self.assertEqual(result["checked_fields"], ["employee_id", "phone", "name"])
        self.assertEqual(result["conflicting_fields"], ["phone", "name"])
        self.assertEqual(result["people"], [])

    def test_consistent_clues_return_the_verified_person(self):
        result = self.store.find(
            employee_id="E003",
            phone="13800138003",
            name="李四",
            department="研发部",
        )

        self.assertEqual(result["status"], "found")
        self.assertEqual(
            result["checked_fields"],
            ["employee_id", "phone", "name", "department"],
        )
        self.assertEqual(result["conflicting_fields"], [])
        self.assertEqual(result["people"][0]["employee_id"], "E003")

    def test_phone_is_primary_but_name_is_still_verified(self):
        result = self.store.find(phone="13800138003", name="张三")

        self.assertEqual(result["status"], "conflicting_clues")
        self.assertEqual(result["matched_by"], "phone")
        self.assertEqual(result["conflicting_fields"], ["name"])
        self.assertEqual(result["people"], [])

    def test_missing_primary_clue_still_detects_secondary_conflict(self):
        result = self.store.find(
            employee_id="E999",
            phone="13800138003",
        )

        self.assertEqual(result["status"], "conflicting_clues")
        self.assertEqual(
            result["conflicting_fields"],
            ["employee_id", "phone"],
        )
        self.assertEqual(result["people"], [])

    def test_name_can_return_multiple_candidates(self):
        result = self.store.find(name="张三")

        self.assertEqual(result["status"], "multiple_matches")
        self.assertEqual(result["matched_by"], "name")
        self.assertEqual(len(result["people"]), 2)

    def test_department_disambiguates_name(self):
        result = self.store.find(name="张三", department="财务部")

        self.assertEqual(result["status"], "found")
        self.assertEqual(result["people"][0]["employee_id"], "E002")

    def test_conflicting_department_does_not_return_a_person(self):
        result = self.store.find(employee_id="E003", department="财务部")

        self.assertEqual(result["status"], "conflicting_clues")
        self.assertEqual(result["conflicting_fields"], ["department"])
        self.assertEqual(result["people"], [])

    def test_directory_crud_persists_and_enforces_uniqueness(self):
        created = self.store.create(
            employee_id="E004",
            name="王五",
            phone="13800138004",
            department="产品部",
        )
        self.assertEqual(created["name"], "王五")
        self.assertEqual(len(self.store.list_all("产品")), 1)

        updated = self.store.update(
            "E004",
            employee_id="E005",
            name="王五",
            phone="13800138005",
            department="市场部",
        )
        self.assertEqual(updated["employee_id"], "E005")
        self.assertEqual(self.store.find(employee_id="E004")["status"], "not_found")
        self.assertEqual(self.store.find(employee_id="E005")["status"], "found")

        with self.assertRaises(DuplicatePersonError):
            self.store.create(
                employee_id="E005",
                name="重复员工",
                phone="13800138999",
                department="市场部",
            )

        self.store.delete("E005")
        with self.assertRaises(PersonNotFoundError):
            self.store.delete("E005")

    def test_tool_exposes_find_person_schema_and_returns_structured_result(self):
        find_person = create_find_person_tool(self.store)

        result = find_person.invoke({"employee_id": " E003 ", "name": "张三"})

        self.assertEqual(find_person.name, "find_person")
        self.assertIn("满足条件就立即调用", find_person.description)
        self.assertIn("把用户明确提供的全部线索如实传入", find_person.description)
        self.assertIn("工具会校验线索是否一致", find_person.description)
        self.assertEqual(result["status"], "conflicting_clues")
        self.assertEqual(result["people"], [])

    def test_agent_executes_find_person_tool_and_returns_final_reply(self):
        tracking_store = TrackingPeopleStore(
            Path(self.temp_dir.name) / "tracked-people.db"
        )
        tracking_store.upsert(
            employee_id="E003",
            name="李四",
            phone="13800138003",
            department="研发部",
        )
        model = ToolCallingFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "find_person",
                            "args": {"employee_id": "E003"},
                            "id": "find-person-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="找到了李四，他在研发部。"),
            ]
        )
        runtime = AgentRuntime(
            model,
            store=ConversationStore(Path(self.temp_dir.name) / "chat.db"),
            tools=[create_find_person_tool(tracking_store)],
        )
        self.addCleanup(runtime.close)

        response = runtime.chat("find-person-session", "帮我找一下工号 E003")

        self.assertEqual(response.reply, "找到了李四，他在研发部。")
        self.assertEqual(len(tracking_store.find_calls), 1)
        self.assertEqual(
            tracking_store.find_calls[0]["employee_id"],
            "E003",
        )


if __name__ == "__main__":
    unittest.main()
