from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import PrivateAttr

from agent import AgentRuntime
from conversation_store import ConversationStore


class RecordingFakeChatModel(FakeListChatModel):
    _recorded_calls: list = PrivateAttr(default_factory=list)

    @property
    def recorded_calls(self):
        return self._recorded_calls

    def _call(self, *args, **kwargs):
        self._recorded_calls.append(args[0])
        return super()._call(*args, **kwargs)


class FakeSummaryModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self._lock = threading.Lock()

    def invoke(self, messages):
        with self._lock:
            self.calls.append(messages)
            return AIMessage(content=next(self.responses))


class BlockingSummaryModel:
    def __init__(self, response="压缩摘要"):
        self.response = response
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release summary model")
        return AIMessage(content=self.response)


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "xiaoyuan.db"
        self.store = ConversationStore(self.db_path)
        self.runtimes = []

    def tearDown(self):
        for runtime in self.runtimes:
            runtime.close()
        self.temp_dir.cleanup()

    def make_runtime(self, chat_model, summary_model=None):
        runtime = AgentRuntime(
            chat_model,
            store=self.store,
            summary_model=summary_model,
        )
        self.runtimes.append(runtime)
        return runtime

    def test_full_transcript_persists_after_store_recreation(self):
        runtime = self.make_runtime(
            FakeListChatModel(responses=["你好，我是小原。", "第二轮回复"])
        )

        first = runtime.chat("persistent", "你好")
        second = runtime.chat("persistent", "继续")

        self.assertEqual(first.round_no, 1)
        self.assertEqual(second.round_no, 2)
        reopened = ConversationStore(self.db_path)
        self.assertEqual(
            [
                (message["round"], message["role"], message["content"])
                for message in reopened.get_messages("persistent")
            ],
            [
                (1, "user", "你好"),
                (1, "assistant", "你好，我是小原。"),
                (2, "user", "继续"),
                (2, "assistant", "第二轮回复"),
            ],
        )
        self.assertEqual(reopened.get_session("persistent")["title"], "你好")

    def test_session_is_created_on_first_send_and_can_be_renamed(self):
        self.assertEqual(self.store.list_sessions(), [])
        self.assertIsNone(self.store.get_session("new-session"))
        runtime = self.make_runtime(
            FakeListChatModel(responses=["第一轮回复", "第二轮回复"])
        )
        runtime.chat(
            "new-session",
            "请帮我整理今天的项目会议。后面还有更多内容",
        )
        auto_titled = self.store.get_session("new-session")
        self.assertEqual(auto_titled["title"], "请帮我整理今天的项目会议")
        self.assertEqual(auto_titled["rounds"], 1)
        self.assertEqual(len(self.store.list_sessions()), 1)

        renamed = self.store.rename_session(
            "new-session",
            "七月项目会议",
        )
        self.assertEqual(renamed["title"], "七月项目会议")
        runtime.chat("new-session", "继续补充")
        self.assertEqual(
            self.store.get_session("new-session")["title"],
            "七月项目会议",
        )
        self.assertEqual(self.store.get_session("new-session")["rounds"], 2)

    def test_threads_are_isolated_and_can_be_reset(self):
        runtime = self.make_runtime(
            FakeListChatModel(responses=["第一条回复", "第二条回复"])
        )

        runtime.chat("first", "第一条")
        runtime.chat("second", "第二条")

        self.assertEqual(runtime.context("first")["rounds"], 1)
        self.assertEqual(runtime.context("second")["rounds"], 1)
        runtime.reset("first")
        self.assertEqual(runtime.context("first")["messages"], [])
        self.assertEqual(runtime.context("second")["rounds"], 1)

    def test_rejects_invalid_input(self):
        runtime = self.make_runtime(
            FakeListChatModel(responses=["unused"])
        )

        with self.assertRaises(ValueError):
            runtime.chat("../bad", "你好")
        with self.assertRaises(ValueError):
            runtime.chat("valid", "   ")

    def test_rolls_summary_forward_and_keeps_full_history(self):
        chat_model = FakeListChatModel(
            responses=[f"回复 {index}" for index in range(1, 51)]
        )
        summary_model = FakeSummaryModel(["第一次摘要", "第二次摘要"])
        runtime = self.make_runtime(chat_model, summary_model)

        for index in range(1, 31):
            runtime.chat("rolling", f"问题 {index}")
        self.assertTrue(runtime.summary_manager.wait_for_idle("rolling"))

        first_context = runtime.context("rolling")
        self.assertEqual(first_context["rounds"], 30)
        self.assertEqual(first_context["summary"], "第一次摘要")
        self.assertEqual(
            first_context["summary_range"],
            {"start_round": 1, "end_round": 20},
        )
        self.assertEqual(first_context["uncovered_rounds"], 10)
        self.assertEqual(len(first_context["messages"]), 60)

        for index in range(31, 51):
            runtime.chat("rolling", f"问题 {index}")
        self.assertTrue(runtime.summary_manager.wait_for_idle("rolling"))

        second_context = runtime.context("rolling")
        self.assertEqual(second_context["rounds"], 50)
        self.assertEqual(second_context["summary"], "第二次摘要")
        self.assertEqual(
            second_context["summary_range"],
            {"start_round": 1, "end_round": 40},
        )
        self.assertEqual(second_context["uncovered_rounds"], 10)
        self.assertEqual(len(second_context["messages"]), 100)
        self.assertEqual(
            [summary.end_round for summary in self.store.get_summary_history("rolling")],
            [20, 40],
        )

        second_summary_input = summary_model.calls[1][-1].content
        self.assertIn("已有摘要：\n第一次摘要", second_summary_input)
        self.assertIn("本次新增原始范围：第 21–40 轮", second_summary_input)
        self.assertIn("第 21 轮用户：问题 21", second_summary_input)
        self.assertIn("第 40 轮助手：回复 40", second_summary_input)

    def test_pending_compression_never_hides_uncovered_full_rounds(self):
        chat_model = RecordingFakeChatModel(
            responses=[f"回复 {index}" for index in range(1, 36)]
        )
        summary_model = BlockingSummaryModel()
        runtime = self.make_runtime(chat_model, summary_model)

        for index in range(1, 31):
            runtime.chat("pending", f"问题 {index}")
        self.assertTrue(summary_model.started.wait(timeout=2))
        self.assertTrue(runtime.context("pending")["compression_pending"])

        for index in range(31, 36):
            runtime.chat("pending", f"问题 {index}")

        # The summary still covers nothing, so turn 35 must receive all 34 complete
        # database rounds plus the current user query.
        last_model_call = chat_model.recorded_calls[-1]
        humans = [
            message.content
            for message in last_model_call
            if isinstance(message, HumanMessage)
        ]
        assistants = [
            message.content
            for message in last_model_call
            if isinstance(message, AIMessage)
        ]
        self.assertEqual(humans[0], "问题 1")
        self.assertEqual(humans[-1], "问题 35")
        self.assertEqual(len(humans), 35)
        self.assertEqual(len(assistants), 34)

        pending_context = runtime.context("pending")
        self.assertIsNone(pending_context["summary_range"])
        self.assertEqual(pending_context["uncovered_rounds"], 35)
        self.assertEqual(len(pending_context["messages"]), 70)

        summary_model.release.set()
        self.assertTrue(runtime.summary_manager.wait_for_idle("pending"))
        completed_context = runtime.context("pending")
        self.assertEqual(
            completed_context["summary_range"],
            {"start_round": 1, "end_round": 20},
        )
        self.assertEqual(completed_context["uncovered_rounds"], 15)


if __name__ == "__main__":
    unittest.main()
