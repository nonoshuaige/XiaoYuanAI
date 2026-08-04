from __future__ import annotations

import threading
import unittest

from app.agent.jobs import ChatJobManager


class ChatJobManagerTests(unittest.TestCase):
    def test_submit_is_idempotent_while_turn_is_active(self):
        started = threading.Event()
        release = threading.Event()
        calls: list[tuple[str, int, str | None]] = []

        def worker(session_id: str, round_no: int, model_id: str | None):
            calls.append((session_id, round_no, model_id))
            started.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test did not release worker")

        manager = ChatJobManager(worker, max_workers=1)
        try:
            self.assertTrue(manager.submit("session", 1, "model"))
            self.assertTrue(started.wait(timeout=1))
            self.assertFalse(manager.submit("session", 1, "model"))
            release.set()
            self.assertTrue(
                manager.wait_for_idle("session", 1, timeout=2)
            )
            self.assertEqual(calls, [("session", 1, "model")])
        finally:
            release.set()
            manager.shutdown()


if __name__ == "__main__":
    unittest.main()
