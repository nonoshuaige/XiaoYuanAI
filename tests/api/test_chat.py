from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as server
from app.agent.jobs import ChatJobCancelledError
from app.persistence.conversations import ConversationStore


class BlockingRuntime:
    def __init__(self, store: ConversationStore):
        self.store = store
        self.started = threading.Event()
        self.release = threading.Event()

    def begin_chat(
        self,
        session_id: str,
        message: str,
        model_id: str | None,
        *,
        job_owner: str = "default",
        context_window_tokens: int = 16_384,
    ) -> tuple[int, str]:
        selected = model_id or "test-model"
        return (
            self.store.begin_round(
                session_id,
                message,
                model_id=selected,
                job_owner=job_owner,
                context_window_tokens=context_window_tokens,
            ),
            selected,
        )

    def complete_chat(
        self,
        session_id: str,
        round_no: int,
        model_id: str | None,
    ) -> None:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release model turn")
        if self.store.get_round(session_id, round_no)["status"] != "pending":
            raise ChatJobCancelledError("对话轮次已取消")
        self.store.complete_round(session_id, round_no, "后台回复")

    def context(self, session_id: str):
        session = self.store.get_session(session_id)
        return {
            "title": session["title"],
            "summary": "",
            "summary_range": None,
            "rounds": self.store.latest_round(session_id),
            "uncovered_rounds": self.store.latest_round(session_id),
            "compression_pending": False,
            "compression_error": None,
            "messages": self.store.get_messages(session_id),
        }


class AsyncChatApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ConversationStore(
            Path(self.temp_dir.name) / "conversation.db"
        )
        self.runtime = BlockingRuntime(self.store)
        self.client = TestClient(server.app)

    def tearDown(self):
        self.runtime.release.set()
        for session_id in ("client-session", "retry-session"):
            for round_no in (1, 2):
                server.chat_job_manager.wait_for_idle(
                    session_id,
                    round_no,
                    timeout=2,
                )
        self.temp_dir.cleanup()

    def test_chat_returns_pending_and_session_recovers_background_result(self):
        with (
            patch.object(server, "conversation_store", self.store),
            patch.object(server, "get_runtime", return_value=self.runtime),
            patch.object(
                server,
                "get_context",
                side_effect=self.runtime.context,
            ),
        ):
            response = self.client.post(
                "/api/chat",
                json={
                    "sessionId": "client-session",
                    "message": "离开页面也要继续",
                    "model": "test-model",
                    "contextWindowTokens": 8192,
                },
            )

            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["status"], "pending")
            self.assertEqual(response.json()["contextWindowTokens"], 8192)
            self.assertNotIn("quickReplies", response.json())
            self.assertTrue(self.runtime.started.wait(timeout=1))
            pending = self.client.get("/api/sessions/client-session")
            self.assertEqual(pending.status_code, 200)
            self.assertEqual(
                pending.json()["messages"][0]["status"],
                "pending",
            )
            self.assertEqual(
                pending.json()["messages"][0]["contextWindowTokens"],
                8192,
            )
            deleting = self.client.delete("/api/sessions/client-session")
            self.assertEqual(deleting.status_code, 409)

            self.runtime.release.set()
            self.assertTrue(
                server.chat_job_manager.wait_for_idle(
                    "client-session",
                    1,
                    timeout=2,
                )
            )
            completed = self.client.get("/api/sessions/client-session")
            self.assertEqual(
                [
                    (message["role"], message["content"])
                    for message in completed.json()["messages"]
                ],
                [
                    ("user", "离开页面也要继续"),
                    ("assistant", "后台回复"),
                ],
            )
            self.assertNotIn(
                "quickReplies",
                completed.json()["messages"][-1],
            )
            events = self.client.get(
                "/api/sessions/client-session/rounds/1/events"
            )
            self.assertEqual(events.status_code, 200)
            self.assertEqual(
                events.headers["content-type"],
                "text/event-stream; charset=utf-8",
            )
            self.assertIn("event: completed", events.text)
            self.assertIn('"content": "后台回复"', events.text)

    def test_pending_round_can_be_cancelled_and_retried(self):
        with (
            patch.object(server, "conversation_store", self.store),
            patch.object(server, "get_runtime", return_value=self.runtime),
            patch.object(
                server,
                "get_context",
                side_effect=self.runtime.context,
            ),
        ):
            first = self.client.post(
                "/api/chat",
                json={
                    "sessionId": "retry-session",
                    "message": "请重试这条消息",
                    "model": "test-model",
                    "contextWindowTokens": 8192,
                },
            )
            self.assertEqual(first.status_code, 202)
            self.assertTrue(self.runtime.started.wait(timeout=1))

            retried = self.client.post(
                "/api/sessions/retry-session/rounds/1/retry"
            )

            self.assertEqual(retried.status_code, 202)
            self.assertEqual(retried.json()["retriedRound"], 1)
            self.assertEqual(retried.json()["round"], 2)
            self.assertEqual(retried.json()["model"], "test-model")
            self.assertEqual(retried.json()["contextWindowTokens"], 8192)
            self.assertEqual(
                self.store.get_round("retry-session", 1)["status"],
                "failed",
            )
            self.assertEqual(
                self.store.get_round("retry-session", 1)["error"],
                "用户取消本轮并重试",
            )
            self.assertEqual(
                self.store.get_round("retry-session", 2)["status"],
                "pending",
            )
            self.assertEqual(
                self.store.get_round_user_message("retry-session", 2),
                "请重试这条消息",
            )

            self.runtime.release.set()
            self.assertTrue(
                server.chat_job_manager.wait_for_idle(
                    "retry-session",
                    2,
                    timeout=2,
                )
            )
            self.assertEqual(
                self.store.get_round("retry-session", 2)["status"],
                "completed",
            )


if __name__ == "__main__":
    unittest.main()
