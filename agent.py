"""Single-agent runtime with SQLite history and asynchronous summarization."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from typing_extensions import NotRequired

from config import get_llm
from conversation_store import (
    ConversationStore,
    ConversationSummary,
    conversation_store,
)


SYSTEM_PROMPT = """你是小原 AI 助手，一个面向中文办公场景的智能助手。

你擅长理解和回答问题，以及完成写作、改写、润色、总结、翻译、信息提炼、内容结构化、
方案草拟和创意讨论等语言任务。

回答时遵循以下原则：
- 默认使用简洁、自然的中文；用户指定其他语言或表达方式时，按用户要求回答。
- 优先理解用户真正想完成的事情，给出清晰、实用、可直接使用的结果。
- 信息不足且会明显影响结果时，提出必要的澄清问题。
- 不编造事实；区分已知信息、合理推断和不确定内容。
"""

SUMMARY_SYSTEM_PROMPT = """你是对话上下文压缩器。请把已有摘要和本次提供的历史对话合并成
一份新的中文摘要，供助手在后续对话中恢复上下文。

摘要必须保留：
- 用户的目标、偏好和明确要求；
- 已确认的事实、约束、决定和重要结论；
- 已完成的工作、关键结果和仍未完成的事项；
- 后续对话需要引用的名称、数据和上下文关系。

删除寒暄、重复表达和不影响后续任务的细节。不要回答用户，不要添加原文没有的信息，
只输出更新后的摘要正文。
"""

MAX_UNCOVERED_ROUNDS = 30
COMPRESS_ROUNDS = 20
KEEP_ROUNDS = MAX_UNCOVERED_ROUNDS - COMPRESS_ROUNDS


class ConversationState(AgentState):
    """Ephemeral model input reconstructed from durable SQLite state."""

    summary: NotRequired[str]


class SummaryContextMiddleware(AgentMiddleware):
    """Inject the durable summary into the model's system context."""

    state_schema = ConversationState

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Any,
    ) -> ModelResponse:
        summary = request.state.get("summary", "").strip()
        if not summary:
            return handler(request)

        base_prompt = request.system_message.text if request.system_message else ""
        return handler(
            request.override(
                system_message=SystemMessage(
                    content=(
                        f"{base_prompt}\n\n"
                        "以下摘要覆盖了更早的对话。请把它作为已经发生的上下文使用，"
                        "不要向用户复述摘要本身：\n"
                        f"{summary}"
                    )
                )
            )
        )


@dataclass(frozen=True)
class AgentResponse:
    reply: str
    session_id: str
    round_no: int


class AsyncSummaryManager:
    """Run cumulative summary jobs without blocking user chat requests."""

    def __init__(
        self,
        store: ConversationStore,
        model: Any,
        *,
        max_workers: int = 2,
    ):
        self.store = store
        self.model = model
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="xiaoyuan-summary",
        )
        self._active_sessions: set[str] = set()
        self._errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def request(self, session_id: str) -> bool:
        """Schedule compression when 30 rounds are not covered by a summary."""
        session_id = self.store.validate_session_id(session_id)
        if not self._needs_compression(session_id):
            return False
        with self._lock:
            if session_id in self._active_sessions:
                return False
            self._active_sessions.add(session_id)
            self._errors.pop(session_id, None)
        self._executor.submit(self._run_session, session_id)
        return True

    def is_active(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._active_sessions

    def wait_for_idle(self, session_id: str, timeout: float = 5.0) -> bool:
        """Test/maintenance helper; normal requests never wait for compression."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_active(session_id):
                return True
            time.sleep(0.01)
        return not self.is_active(session_id)

    def last_error(self, session_id: str) -> str | None:
        with self._lock:
            return self._errors.get(session_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)

    def _needs_compression(self, session_id: str) -> bool:
        summary = self.store.get_latest_summary(session_id)
        covered_through = summary.end_round if summary else 0
        return (
            self.store.latest_round(session_id) - covered_through
            >= MAX_UNCOVERED_ROUNDS
        )

    def _run_session(self, session_id: str) -> None:
        failed = False
        try:
            while self._compress_next_block(session_id):
                pass
        except Exception as exc:
            failed = True
            with self._lock:
                self._errors[session_id] = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._active_sessions.discard(session_id)
            # Close the append-vs-finish race: if a new round arrived while this
            # worker was exiting, immediately schedule the next worker.
            if not failed:
                self.request(session_id)

    def _compress_next_block(self, session_id: str) -> bool:
        previous = self.store.get_latest_summary(session_id)
        previous_end = previous.end_round if previous else 0
        latest_round = self.store.latest_round(session_id)
        if latest_round - previous_end < MAX_UNCOVERED_ROUNDS:
            return False

        target_end = previous_end + COMPRESS_ROUNDS
        source_messages = self.store.get_messages(
            session_id,
            after_round=previous_end,
            through_round=target_end,
        )
        if not _contains_complete_rounds(source_messages, COMPRESS_ROUNDS):
            return False

        updated_summary = self._create_summary(
            previous=previous,
            messages=source_messages,
            target_end=target_end,
        )
        return self.store.save_summary(
            session_id=session_id,
            content=updated_summary,
            expected_previous_end=previous_end,
            end_round=target_end,
        )

    def _create_summary(
        self,
        *,
        previous: ConversationSummary | None,
        messages: list[dict[str, Any]],
        target_end: int,
    ) -> str:
        previous_text = previous.content if previous else "无"
        previous_end = previous.end_round if previous else 0
        previous_range = f"第 1–{previous_end} 轮" if previous else "无"
        transcript = "\n".join(
            f"第 {message['round']} 轮{_role_label(message['role'])}："
            f"{message['content']}"
            for message in messages
        )
        response = self.model.invoke(
            [
                SystemMessage(content=SUMMARY_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"已有摘要覆盖范围：{previous_range}\n"
                        f"已有摘要：\n{previous_text}\n\n"
                        f"本次新增原始范围：第 "
                        f"{previous_end + 1}"
                        f"–{target_end} 轮\n"
                        f"{transcript}"
                    )
                ),
            ]
        )
        summary = _content_text(response.content).strip()
        if not summary:
            raise RuntimeError("摘要模型没有返回可用文本")
        return summary


class AgentRuntime:
    """Rebuild every model context from the durable summary and full transcript."""

    def __init__(
        self,
        model: Any,
        *,
        store: ConversationStore = conversation_store,
        summary_model: Any | None = None,
        summary_manager: AsyncSummaryManager | None = None,
    ):
        self.store = store
        self.summary_middleware = SummaryContextMiddleware()
        self.graph = create_agent(
            model=model,
            tools=[],
            system_prompt=SYSTEM_PROMPT,
            middleware=[self.summary_middleware],
            state_schema=ConversationState,
            name="xiaoyuan",
        )
        self.summary_manager = summary_manager or AsyncSummaryManager(
            store,
            summary_model or model,
        )
        self._session_locks: dict[str, threading.Lock] = {}
        self._session_locks_guard = threading.Lock()

    def chat(self, session_id: str, user_message: str) -> AgentResponse:
        session_id = self.store.validate_session_id(session_id)
        message = user_message.strip()
        if not message:
            raise ValueError("消息不能为空")

        # Serialize model turns within one session. Compression remains asynchronous
        # and never holds this lock.
        with self._session_lock(session_id):
            summary, uncovered_messages = self._load_model_context(session_id)
            result = self.graph.invoke(
                {
                    "summary": summary.content if summary else "",
                    "messages": [
                        *_to_langchain_messages(uncovered_messages),
                        HumanMessage(content=message),
                    ],
                }
            )
            reply = _last_reply(result["messages"])
            round_no = self.store.append_round(session_id, message, reply)

        self.summary_manager.request(session_id)
        return AgentResponse(
            reply=reply,
            session_id=session_id,
            round_no=round_no,
        )

    def _load_model_context(
        self,
        session_id: str,
    ) -> tuple[ConversationSummary | None, list[dict[str, Any]]]:
        """Load every full round not yet represented by the latest summary."""
        summary = self.store.get_latest_summary(session_id)
        covered_through = summary.end_round if summary else 0
        messages = self.store.get_messages(
            session_id,
            after_round=covered_through,
        )
        return summary, messages

    def context(self, session_id: str) -> dict[str, Any]:
        session_id = self.store.validate_session_id(session_id)
        session = self.store.get_session(session_id)
        summary = self.store.get_latest_summary(session_id)
        latest_round = self.store.latest_round(session_id)
        covered_through = summary.end_round if summary else 0
        return {
            "title": session["title"] if session else "新对话",
            "summary": summary.content if summary else "",
            "summary_range": (
                {
                    "start_round": summary.start_round,
                    "end_round": summary.end_round,
                }
                if summary
                else None
            ),
            "rounds": latest_round,
            "uncovered_rounds": latest_round - covered_through,
            "compression_pending": self.summary_manager.is_active(session_id),
            "compression_error": self.summary_manager.last_error(session_id),
            "messages": self.store.get_messages(session_id),
        }

    def reset(self, session_id: str) -> None:
        self.store.delete_session(session_id)

    def close(self) -> None:
        self.summary_manager.shutdown()

    def _session_lock(self, session_id: str) -> threading.Lock:
        with self._session_locks_guard:
            return self._session_locks.setdefault(session_id, threading.Lock())


_default_runtime: AgentRuntime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> AgentRuntime:
    """Lazily create the production runtime so imports do not require a key."""
    global _default_runtime
    if _default_runtime is None:
        with _runtime_lock:
            if _default_runtime is None:
                model = get_llm()
                _default_runtime = AgentRuntime(model)
    return _default_runtime


def get_context(session_id: str) -> dict[str, Any]:
    return get_runtime().context(session_id)


def reset_session(session_id: str) -> None:
    conversation_store.validate_session_id(session_id)
    if _default_runtime is None:
        conversation_store.delete_session(session_id)
        return
    _default_runtime.reset(session_id)


def _to_langchain_messages(
    messages: list[dict[str, Any]],
) -> list[BaseMessage]:
    return [
        (
            HumanMessage(content=message["content"])
            if message["role"] == "user"
            else AIMessage(content=message["content"])
        )
        for message in messages
    ]


def _contains_complete_rounds(
    messages: list[dict[str, Any]],
    expected_rounds: int,
) -> bool:
    roles_by_round: dict[int, set[str]] = {}
    for message in messages:
        roles_by_round.setdefault(message["round"], set()).add(message["role"])
    return (
        len(roles_by_round) == expected_rounds
        and all(roles == {"user", "assistant"} for roles in roles_by_round.values())
    )


def _last_reply(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = _content_text(message.content).strip()
            if text:
                return text
    raise RuntimeError("模型没有返回可用文本")


def _role_label(role: str) -> str:
    return "用户" if role == "user" else "助手"


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)
    if isinstance(content, (dict, tuple)):
        return json.dumps(content, ensure_ascii=False)
    return str(content)
