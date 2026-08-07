from __future__ import annotations

import unittest

from app.agent.event_buffer import ChatEventBuffer


class ChatEventBufferTests(unittest.TestCase):
    def test_keeps_live_events_until_terminal_retention_expires(self):
        now = [10.0]
        buffer = ChatEventBuffer(
            retention_seconds=5,
            clock=lambda: now[0],
        )

        buffer.append("session", 1, "text_delta", {"delta": "你好"})
        buffer.append("session", 1, "completed", {"content": "你好"})

        self.assertEqual(
            [event["type"] for event in buffer.get("session", 1)],
            ["text_delta", "completed"],
        )
        now[0] = 15.0
        self.assertEqual(buffer.get("session", 1), [])

    def test_reads_after_cursor_and_clears_a_session(self):
        buffer = ChatEventBuffer()
        first = buffer.append("session", 1, "status", {})
        buffer.append("session", 1, "text_delta", {"delta": "回答"})

        self.assertEqual(
            [
                event["type"]
                for event in buffer.get(
                    "session",
                    1,
                    after_event_id=first,
                )
            ],
            ["text_delta"],
        )
        buffer.clear_session("session")
        self.assertEqual(buffer.get("session", 1), [])


if __name__ == "__main__":
    unittest.main()
