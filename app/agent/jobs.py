"""Process-local workers for durable asynchronous chat turns."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable


ChatWorker = Callable[[str, int, str | None], object]
LOGGER = logging.getLogger(__name__)


class ChatJobCancelledError(RuntimeError):
    """A durable chat round was cancelled while its worker was still active."""


class ChatJobManager:
    """Run each persisted chat turn once while this service is alive."""

    def __init__(
        self,
        worker: ChatWorker,
        *,
        max_workers: int = 4,
    ):
        self._worker = worker
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="xiaoyuan-chat",
        )
        self._active: set[tuple[str, int]] = set()
        self._condition = threading.Condition()

    def submit(
        self,
        session_id: str,
        round_no: int,
        model_id: str | None,
    ) -> bool:
        """Schedule a durable turn unless this process already owns it."""
        key = (session_id, round_no)
        with self._condition:
            if key in self._active:
                return False
            self._active.add(key)
        try:
            self._executor.submit(
                self._run,
                key,
                model_id,
            )
        except Exception:
            with self._condition:
                self._active.discard(key)
                self._condition.notify_all()
            raise
        return True

    def is_active(self, session_id: str, round_no: int) -> bool:
        with self._condition:
            return (session_id, round_no) in self._active

    def wait_for_idle(
        self,
        session_id: str,
        round_no: int,
        *,
        timeout: float = 5.0,
    ) -> bool:
        """Wait helper for integration tests and controlled shutdown checks."""
        key = (session_id, round_no)
        deadline = time.monotonic() + timeout
        with self._condition:
            while key in self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
        return True

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)

    def _run(
        self,
        key: tuple[str, int],
        model_id: str | None,
    ) -> None:
        session_id, round_no = key
        try:
            self._worker(session_id, round_no, model_id)
        except ChatJobCancelledError:
            # Cancellation is an expected terminal state, not a worker failure.
            pass
        except Exception:
            # AgentRuntime persists the failed round before propagating.
            LOGGER.exception(
                "Background chat turn failed",
                extra={
                    "session_id": session_id,
                    "round_no": round_no,
                },
            )
        finally:
            with self._condition:
                self._active.discard(key)
                self._condition.notify_all()


__all__ = ["ChatJobCancelledError", "ChatJobManager"]
