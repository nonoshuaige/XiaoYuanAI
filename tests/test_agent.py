from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import PrivateAttr

from agent import AgentRuntime
from conversation_store import ConversationStore
from people_tool import PeopleStore, create_find_person_tool
from prompts import (
    SUMMARY_SYSTEM_PROMPT,
    build_current_time_context,
    build_system_prompt,
)
from meeting_room_tool import (
    MeetingRoomStore,
    SandboxMeetingRoomClient,
    create_meeting_room_tools,
)
from meeting_room_skill import create_meeting_room_skill


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


class FailingAfterPersistenceModel(FakeListChatModel):
    _store: ConversationStore = PrivateAttr()
    _session_id: str = PrivateAttr()
    _saw_persisted_user: bool = PrivateAttr(default=False)

    def __init__(self, store, session_id):
        super().__init__(responses=["unused"])
        self._store = store
        self._session_id = session_id

    @property
    def saw_persisted_user(self):
        return self._saw_persisted_user

    def _call(self, *args, **kwargs):
        messages = self._store.get_messages(self._session_id)
        self._saw_persisted_user = bool(
            messages
            and messages[-1]["role"] == "user"
            and messages[-1]["status"] == "pending"
        )
        raise RuntimeError("模拟模型失败")


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

    def test_system_prompt_separates_general_and_tool_capabilities(self):
        prompt = build_system_prompt([])

        self.assertIn("不把这些通用能力描述成工具", prompt)
        self.assertIn("当前没有接入任何外部工具", prompt)
        self.assertIn("不展示工具名、参数或调用过程", prompt)

    def test_system_prompt_does_not_duplicate_bound_tool_description(self):
        people_store = PeopleStore(Path(self.temp_dir.name) / "people.db")
        find_person = create_find_person_tool(people_store)
        prompt = build_system_prompt([find_person])

        self.assertNotIn("当前没有接入任何外部工具", prompt)
        self.assertNotIn(find_person.description, prompt)
        self.assertIn("本轮实际提供的工具", prompt)

    def test_system_prompt_handles_greetings_and_self_introductions(self):
        people_store = PeopleStore(Path(self.temp_dir.name) / "people.db")
        prompt = build_system_prompt(
            [create_find_person_tool(people_store)]
        )

        self.assertIn("仅作普通问候时自然回应", prompt)
        self.assertIn("仅作自我介绍时友好回应", prompt)
        self.assertIn("姓名只是对话内容，不代表查询请求", prompt)
        self.assertIn("不要暗示已经查询或没有查询到该用户", prompt)
        self.assertIn("若还包含明确任务，则继续完成该任务", prompt)
        self.assertIn("只有工具实际返回结果", prompt)

    def test_system_prompt_lists_meeting_tools_only_when_registered(self):
        meeting_store = MeetingRoomStore(
            Path(self.temp_dir.name) / "meeting-rooms.db"
        )
        prompt_without_tools = build_system_prompt([])
        skill = create_meeting_room_skill(
            SandboxMeetingRoomClient(meeting_store)
        )
        prompt_with_tools = build_system_prompt(
            list(skill.tools),
            [skill],
        )

        self.assertNotIn("queryMeetingRooms", prompt_without_tools)
        self.assertNotIn("bookMeetingRoom", prompt_without_tools)
        self.assertIn("meeting-room-booking", prompt_with_tools)
        self.assertIn("`queryMeetingRooms`, `bookMeetingRoom`", prompt_with_tools)
        self.assertIn(
            "时间、楼层、房间名称/编号中的任一线索",
            prompt_with_tools,
        )
        self.assertIn(
            "用户只提供时间时，查询全部楼层",
            prompt_with_tools,
        )
        self.assertIn("suggestedTimeRanges", prompt_with_tools)
        self.assertIn("生成待确认预约卡片", prompt_with_tools)
        self.assertIn("当前半小时槽之前的历史预约不再复述", prompt_with_tools)
        self.assertIn("不在普通回复中重复", prompt_with_tools)
        self.assertIn("本轮服务端时间上下文", prompt_with_tools)
        self.assertIn("不得推荐或提交", prompt_with_tools)

    def test_runtime_registers_skill_tools_and_constraints_together(self):
        meeting_store = MeetingRoomStore(
            Path(self.temp_dir.name) / "meeting-skill.db"
        )
        skill = create_meeting_room_skill(
            SandboxMeetingRoomClient(meeting_store)
        )
        runtime = AgentRuntime(
            FakeListChatModel(responses=["好的"]),
            store=self.store,
            skills=[skill],
        )
        self.runtimes.append(runtime)

        self.assertEqual(
            [registered_tool.name for registered_tool in runtime.tools],
            ["queryMeetingRooms", "bookMeetingRoom"],
        )
        graph_prompt = build_system_prompt(runtime.tools, runtime.skills)
        self.assertIn("模型也不能代替用户执行最终预约", graph_prompt)
        self.assertIn("卡片返回真实bookingId和meetingId", graph_prompt)

    def test_runtime_injects_fresh_server_time_before_each_turn(self):
        model = RecordingFakeChatModel(responses=["第一轮", "第二轮"])
        moments = iter(
            [
                datetime.fromisoformat("2026-07-28T23:59:58+08:00"),
                datetime.fromisoformat("2026-07-29T00:00:02+08:00"),
            ]
        )
        runtime = AgentRuntime(
            model,
            store=self.store,
            runtime_context_hooks=[
                lambda: SystemMessage(
                    content=build_current_time_context(next(moments))
                )
            ],
        )
        self.runtimes.append(runtime)

        runtime.chat("clock-hook", "今天七楼有会议室吗")
        runtime.chat("clock-hook", "那明天呢")

        first_system = [
            message.content
            for message in model.recorded_calls[0]
            if isinstance(message, SystemMessage)
        ]
        second_system = [
            message.content
            for message in model.recorded_calls[1]
            if isinstance(message, SystemMessage)
        ]
        self.assertTrue(
            any("当前日期：2026/07/28" in content for content in first_system)
        )
        self.assertTrue(
            any("当前时间：23:59:58" in content for content in first_system)
        )
        self.assertTrue(
            any("当前日期：2026/07/29" in content for content in second_system)
        )
        self.assertTrue(
            any("当前时间：00:00:02" in content for content in second_system)
        )

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
        self.assertEqual(
            reopened.get_round("persistent", 1)["status"],
            "completed",
        )

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

    def test_selects_requested_model_and_rejects_unknown_model(self):
        default_model = FakeListChatModel(responses=["默认模型回复"])
        alternate_model = FakeListChatModel(responses=["第二模型回复"])
        runtime = AgentRuntime(
            default_model,
            store=self.store,
            models={
                "qwen-coder": default_model,
                "qwen3d6-27b": alternate_model,
            },
            default_model_id="qwen-coder",
        )
        self.runtimes.append(runtime)

        first = runtime.chat("models", "使用默认模型")
        second = runtime.chat("models", "切换模型", "qwen3d6-27b")

        self.assertEqual(first.reply, "默认模型回复")
        self.assertEqual(second.reply, "第二模型回复")
        with self.assertRaisesRegex(ValueError, "不支持的模型"):
            runtime.chat("models", "未知模型", "not-configured")
        self.assertEqual(self.store.latest_round("models"), 2)

    def test_lazily_loads_and_caches_each_selected_model(self):
        available_models = {
            "default-model": FakeListChatModel(
                responses=["默认回复一", "默认回复二"]
            ),
            "alternate-model": FakeListChatModel(responses=["备选回复"]),
        }
        factory_calls = []

        def model_factory(model_id):
            factory_calls.append(model_id)
            return available_models[model_id]

        runtime = AgentRuntime(
            store=self.store,
            model_factory=model_factory,
            default_model_id="default-model",
        )
        self.runtimes.append(runtime)

        self.assertEqual(factory_calls, [])
        self.assertEqual(runtime.graphs, {})

        runtime.chat("lazy-models", "第一轮")
        runtime.chat("lazy-models", "第二轮")
        runtime.chat("lazy-models", "第三轮", "alternate-model")

        self.assertEqual(
            factory_calls,
            ["default-model", "alternate-model"],
        )
        self.assertEqual(
            set(runtime.graphs),
            {"default-model", "alternate-model"},
        )

    def test_persists_complete_langchain_ai_message_for_each_round(self):
        rich_message = AIMessage(
            content="带审计信息的回复",
            additional_kwargs={
                "reasoning_content": "内部推理字段",
                "provider_extension": {"trace": "trace-1"},
            },
            response_metadata={
                "finish_reason": "stop",
                "model_name": "audit-model",
                "token_usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            },
            id="run-audit-1",
            usage_metadata={
                "input_tokens": 11,
                "output_tokens": 7,
                "total_tokens": 18,
            },
        )
        runtime = self.make_runtime(
            FakeMessagesListChatModel(responses=[rich_message])
        )

        runtime.chat("audit-session", "记录完整响应")

        calls = self.store.get_model_calls("audit-session")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["modelId"], "default")
        self.assertEqual(calls[0]["status"], "completed")
        self.assertEqual(calls[0]["providerResponses"], [])
        converted = calls[0]["langchainAIMessage"]
        self.assertEqual(converted["content"], "带审计信息的回复")
        self.assertEqual(
            converted["additional_kwargs"]["reasoning_content"],
            "内部推理字段",
        )
        self.assertEqual(
            converted["response_metadata"]["finish_reason"],
            "stop",
        )
        self.assertEqual(converted["usage_metadata"]["total_tokens"], 18)
        self.assertEqual(converted["id"], "run-audit-1")

    def test_user_message_is_persisted_before_model_call_and_kept_on_failure(self):
        model = FailingAfterPersistenceModel(self.store, "failed-turn")
        runtime = self.make_runtime(model)

        with self.assertRaisesRegex(RuntimeError, "模拟模型失败"):
            runtime.chat("failed-turn", "这条消息必须先保存")

        self.assertTrue(model.saw_persisted_user)
        self.assertEqual(
            [
                (
                    message["round"],
                    message["role"],
                    message["content"],
                    message["status"],
                )
                for message in self.store.get_messages("failed-turn")
            ],
            [(1, "user", "这条消息必须先保存", "failed")],
        )
        failed_round = self.store.get_round("failed-turn", 1)
        self.assertEqual(failed_round["status"], "failed")
        self.assertIn("模拟模型失败", failed_round["error"])
        failed_calls = self.store.get_model_calls("failed-turn")
        self.assertEqual(len(failed_calls), 1)
        self.assertEqual(failed_calls[0]["status"], "failed")
        self.assertIsNone(failed_calls[0]["langchainAIMessage"])
        self.assertIn("模拟模型失败", failed_calls[0]["error"])

    def test_summary_is_user_context_and_never_changes_system_prompt(self):
        for index in range(1, 21):
            self.store.append_round(
                "summary-role",
                f"历史问题 {index}",
                f"历史回复 {index}",
            )
        self.assertTrue(
            self.store.save_summary(
                session_id="summary-role",
                content="用户最终选择蓝色方案",
                expected_previous_end=0,
                end_round=20,
            )
        )
        model = RecordingFakeChatModel(responses=["继续回复"])
        runtime = self.make_runtime(model)

        runtime.chat("summary-role", "继续处理")

        model_call = model.recorded_calls[-1]
        system_contents = [
            message.content
            for message in model_call
            if isinstance(message, SystemMessage)
        ]
        human_contents = [
            message.content
            for message in model_call
            if isinstance(message, HumanMessage)
        ]
        self.assertTrue(system_contents)
        self.assertFalse(
            any("用户最终选择蓝色方案" in content for content in system_contents)
        )
        self.assertEqual(
            sum("用户最终选择蓝色方案" in content for content in human_contents),
            1,
        )
        self.assertIn("用户层上下文", human_contents[0])
        self.assertEqual(human_contents.count("继续处理"), 1)

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
        self.assertIn("第 21 轮[记录时间：", second_summary_input)
        self.assertIn("用户：问题 21", second_summary_input)
        self.assertIn("第 40 轮[记录时间：", second_summary_input)
        self.assertIn("助手：回复 40", second_summary_input)
        self.assertIn("Asia/Shanghai", second_summary_input)

    def test_summary_prompt_absolutizes_relative_dates_from_message_time(self):
        self.assertIn("该条消息自己的记录时间", SUMMARY_SYSTEM_PROMPT)
        self.assertIn("yyyy/MM/dd", SUMMARY_SYSTEM_PROMPT)
        self.assertIn("不得使用生成摘要时的当前时间", SUMMARY_SYSTEM_PROMPT)
        self.assertIn("日期未确认", SUMMARY_SYSTEM_PROMPT)

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
