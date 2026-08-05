from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Sequence

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from app.agent.runtime import AgentRuntime
from app.persistence.conversations import ConversationStore
from app.features.people.tools import FindPersonInput, create_find_person_tool


class FakeExternalDirectory:
    def __init__(self):
        self.find_calls: list[dict[str, str | None]] = []

    def find(self, **kwargs):
        self.find_calls.append(kwargs)
        if kwargs.get("employee_id") == "160218":
            return {
                "status": "found",
                "matched_by": "employee_id",
                "checked_fields": ["employee_id"],
                "conflicting_fields": [],
                "people": [
                    {
                        "employee_id": "160218",
                        "name": "程少伟",
                        "phone": "13800000008",
                        "department": "数字化部",
                    }
                ],
                "message": "已从外部通讯录找到 1 位员工。",
                "source": "mock-sandbox",
            }
        return {
            "status": "not_found",
            "matched_by": "employee_id",
            "checked_fields": ["employee_id"],
            "conflicting_fields": [],
            "people": [],
            "message": "外部通讯录没有匹配员工。",
            "source": "mock-sandbox",
        }


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ):
        return self


class PeopleToolTests(unittest.TestCase):
    def test_requires_an_identity_clue(self):
        with self.assertRaisesRegex(ValidationError, "至少需要提供一个"):
            FindPersonInput(department="数字化部")

    def test_tool_delegates_to_external_directory(self):
        directory = FakeExternalDirectory()
        find_person = create_find_person_tool(directory)

        result = find_person.invoke({"employee_id": " 160218 "})

        self.assertEqual(find_person.name, "find_person")
        self.assertIn("查询员工人事信息", find_person.description)
        self.assertIn("如果都没有，先引导用户补充", find_person.description)
        self.assertIn("必须展示全部 people 及匹配来源", find_person.description)
        self.assertIn("status=not_found，表示该线索", find_person.description)
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["source"], "mock-sandbox")
        self.assertEqual(directory.find_calls[0]["employee_id"], "160218")

    def test_agent_executes_external_find_person_tool(self):
        directory = FakeExternalDirectory()
        model = ToolCallingFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "find_person",
                            "args": {"employee_id": "160218"},
                            "id": "find-person-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="找到了程少伟，他在数字化部。"),
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir) / "chat.db")
            runtime = AgentRuntime(
                model,
                store=store,
                tools=[create_find_person_tool(directory)],
            )
            self.addCleanup(runtime.close)

            response = runtime.chat("find-person-session", "找工号160218")

            self.assertEqual(response.reply, "找到了程少伟，他在数字化部。")
            self.assertEqual(len(directory.find_calls), 1)
            events = store.get_chat_events("find-person-session", 1)
            self.assertEqual(
                [
                    event["type"]
                    for event in events
                    if event["type"].startswith("tool_")
                ],
                ["tool_start", "tool_end"],
            )


if __name__ == "__main__":
    unittest.main()
