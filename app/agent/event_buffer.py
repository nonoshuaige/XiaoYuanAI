"""Short-lived in-memory events used only for live Agent SSE delivery."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


TERMINAL_EVENT_TYPES = frozenset({"completed", "failed"})


@dataclass
class _RoundEventState:
    events: list[dict[str, Any]] = field(default_factory=list)
    next_event_id: int = 1
    terminal_at: float | None = None


class ChatEventBuffer:
    """Keep reconnectable stream fragments briefly without database writes."""

    def __init__(
        self,
        *,
        retention_seconds: float = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if retention_seconds < 0:
            raise ValueError("retention_seconds 不能小于 0")
        self.retention_seconds = retention_seconds
        self._clock = clock
        self._rounds: dict[tuple[str, int], _RoundEventState] = {}
        self._lock = threading.RLock()

    def append(
        self,
        session_id: str,
        round_no: int,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        resolved_type = event_type.strip()
        if not resolved_type or len(resolved_type) > 64:
            raise ValueError("event_type 必须为 1 到 64 个字符")
        if round_no < 1:
            raise ValueError("round_no 必须大于 0")
        now = self._clock()
        with self._lock:
            self._prune(now)
            state = self._rounds.setdefault(
                (session_id, round_no),
                _RoundEventState(),
            )
            event_id = state.next_event_id
            state.next_event_id += 1
            state.events.append(
                {
                    "eventId": event_id,
                    "type": resolved_type,
                    "payload": payload or {},
                    "created_at": datetime.now().isoformat(),
                }
            )
            if resolved_type in TERMINAL_EVENT_TYPES:
                state.terminal_at = now
            return event_id

    def get(
        self,
        session_id: str,
        round_no: int,
        *,
        after_event_id: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if round_no < 1:
            raise ValueError("round_no 必须大于 0")
        if after_event_id < 0:
            raise ValueError("after_event_id 不能小于 0")
        resolved_limit = max(1, min(limit, 1_000))
        with self._lock:
            self._prune(self._clock())
            state = self._rounds.get((session_id, round_no))
            if state is None:
                return []
            return [
                event.copy()
                for event in state.events
                if event["eventId"] > after_event_id
            ][:resolved_limit]

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            keys = [key for key in self._rounds if key[0] == session_id]
            for key in keys:
                self._rounds.pop(key, None)

    def _prune(self, now: float) -> None:
        expired = [
            key
            for key, state in self._rounds.items()
            if state.terminal_at is not None
            and now - state.terminal_at >= self.retention_seconds
        ]
        for key in expired:
            self._rounds.pop(key, None)


chat_event_buffer = ChatEventBuffer()
