"""SQLite persistence for complete conversations and rolling summaries."""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).parent / "data"
DEFAULT_DB_PATH = DATA_DIR / "xiaoyuan.db"
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ConversationSummary:
    session_id: str
    content: str
    start_round: int
    end_round: int
    created_at: str


class ConversationStore:
    """Keep the full transcript and cumulative summary versions in SQLite."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self._init_schema()

    @staticmethod
    def validate_session_id(session_id: str) -> str:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError(
                "sessionId 只能包含字母、数字、下划线和短横线，长度不超过 64"
            )
        return session_id

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '新对话',
                    round_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chat_messages (
                    session_id TEXT NOT NULL,
                    round_no INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, round_no, role),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_chat_messages_session_round
                ON chat_messages(session_id, round_no);

                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    start_round INTEGER NOT NULL,
                    end_round INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, end_round),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_summaries_session_end
                ON conversation_summaries(session_id, end_round DESC);
                """
            )
            session_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(sessions)")
            }
            if "title" not in session_columns:
                connection.execute(
                    """
                    ALTER TABLE sessions
                    ADD COLUMN title TEXT NOT NULL DEFAULT '新对话'
                    """
                )
            if "round_count" not in session_columns:
                connection.execute(
                    """
                    ALTER TABLE sessions
                    ADD COLUMN round_count INTEGER NOT NULL DEFAULT 0
                    """
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_updated
                ON sessions(updated_at DESC)
                """
            )
            schema_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if schema_version < 1:
                untitled_sessions = connection.execute(
                    """
                    SELECT s.session_id, m.content
                    FROM sessions AS s
                    JOIN chat_messages AS m
                        ON m.session_id = s.session_id
                        AND m.round_no = 1
                        AND m.role = 'user'
                    WHERE s.title = '新对话'
                    """
                ).fetchall()
                connection.executemany(
                    "UPDATE sessions SET title = ? WHERE session_id = ?",
                    [
                        (
                            _title_from_first_message(row["content"]),
                            row["session_id"],
                        )
                        for row in untitled_sessions
                    ],
                )
                connection.execute("PRAGMA user_version = 1")
            if schema_version < 2:
                connection.execute(
                    """
                    UPDATE sessions
                    SET round_count = COALESCE(
                        (
                            SELECT MAX(m.round_no)
                            FROM chat_messages AS m
                            WHERE m.session_id = sessions.session_id
                        ),
                        0
                    )
                    """
                )
                # Empty sessions belonged to the previous eager-creation design.
                # The UI now represents "新对话" virtually until first send.
                connection.execute(
                    "DELETE FROM sessions WHERE round_count = 0"
                )
                connection.execute("PRAGMA user_version = 2")

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        session_id = self.validate_session_id(session_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    session_id,
                    title,
                    round_count AS rounds,
                    created_at,
                    updated_at
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return self._session_row(row) if row else None

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    session_id,
                    title,
                    round_count AS rounds,
                    created_at,
                    updated_at
                FROM sessions
                WHERE round_count > 0
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [self._session_row(row) for row in rows]

    def rename_session(
        self,
        session_id: str,
        title: str,
    ) -> dict[str, Any] | None:
        session_id = self.validate_session_id(session_id)
        resolved_title = " ".join(title.split()).strip()
        if not resolved_title:
            raise ValueError("会话名称不能为空")
        if len(resolved_title) > 80:
            raise ValueError("会话名称不能超过 80 个字符")
        now = datetime.now().isoformat()
        with self._write_lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sessions
                SET title = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (resolved_title, now, session_id),
            )
        return self.get_session(session_id) if cursor.rowcount else None

    def append_round(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
    ) -> int:
        """Atomically append one complete user/assistant round."""
        session_id = self.validate_session_id(session_id)
        now = datetime.now().isoformat()
        with self._write_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO sessions(
                    session_id, title, round_count, created_at, updated_at
                )
                VALUES (?, '新对话', 0, ?, ?)
                ON CONFLICT(session_id) DO NOTHING
                """,
                (session_id, now, now),
            )
            row = connection.execute(
                """
                SELECT round_count + 1 AS next_round
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            round_no = int(row["next_round"])
            connection.executemany(
                """
                INSERT INTO chat_messages(
                    session_id, round_no, role, content, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (session_id, round_no, "user", user_content, now),
                    (
                        session_id,
                        round_no,
                        "assistant",
                        assistant_content,
                        now,
                    ),
                ],
            )
            connection.execute(
                """
                UPDATE sessions
                SET
                    title = CASE
                        WHEN ? = 1 AND title = '新对话' THEN ?
                        ELSE title
                    END,
                    round_count = ?,
                    updated_at = ?
                WHERE session_id = ?
                """,
                (
                    round_no,
                    _title_from_first_message(user_content),
                    round_no,
                    now,
                    session_id,
                ),
            )
        return round_no

    def get_messages(
        self,
        session_id: str,
        *,
        after_round: int = 0,
        through_round: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read complete transcript rows in stable user-then-assistant order."""
        session_id = self.validate_session_id(session_id)
        query = """
            SELECT round_no, role, content, created_at
            FROM chat_messages
            WHERE session_id = ? AND round_no > ?
        """
        parameters: list[Any] = [session_id, after_round]
        if through_round is not None:
            query += " AND round_no <= ?"
            parameters.append(through_round)
        query += """
            ORDER BY
                round_no,
                CASE role WHEN 'user' THEN 0 ELSE 1 END
        """
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                "round": row["round_no"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def latest_round(self, session_id: str) -> int:
        session_id = self.validate_session_id(session_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(round_no), 0) AS latest_round
                FROM chat_messages
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return int(row["latest_round"])

    def get_latest_summary(
        self,
        session_id: str,
    ) -> ConversationSummary | None:
        session_id = self.validate_session_id(session_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_id, content, start_round, end_round, created_at
                FROM conversation_summaries
                WHERE session_id = ?
                ORDER BY end_round DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return ConversationSummary(
            session_id=row["session_id"],
            content=row["content"],
            start_round=row["start_round"],
            end_round=row["end_round"],
            created_at=row["created_at"],
        )

    def save_summary(
        self,
        *,
        session_id: str,
        content: str,
        expected_previous_end: int,
        end_round: int,
    ) -> bool:
        """Commit a cumulative summary only if its predecessor is still current."""
        session_id = self.validate_session_id(session_id)
        now = datetime.now().isoformat()
        with self._write_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT COALESCE(MAX(end_round), 0) AS current_end
                FROM conversation_summaries
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if int(row["current_end"]) != expected_previous_end:
                return False
            connection.execute(
                """
                INSERT INTO conversation_summaries(
                    session_id, content, start_round, end_round, created_at
                ) VALUES (?, ?, 1, ?, ?)
                """,
                (session_id, content, end_round, now),
            )
        return True

    def get_summary_history(
        self,
        session_id: str,
    ) -> list[ConversationSummary]:
        session_id = self.validate_session_id(session_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, content, start_round, end_round, created_at
                FROM conversation_summaries
                WHERE session_id = ?
                ORDER BY end_round
                """,
                (session_id,),
            ).fetchall()
        return [
            ConversationSummary(
                session_id=row["session_id"],
                content=row["content"],
                start_round=row["start_round"],
                end_round=row["end_round"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def delete_session(self, session_id: str) -> bool:
        session_id = self.validate_session_id(session_id)
        with self._write_lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _session_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "sessionId": row["session_id"],
            "title": row["title"],
            "rounds": int(row["rounds"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


def _title_from_first_message(content: str) -> str:
    """Use the first user sentence as a compact deterministic session title."""
    normalized = " ".join(content.split()).strip()
    first_sentence = re.split(r"[。！？!?]+", normalized, maxsplit=1)[0].strip()
    title = first_sentence or normalized or "新对话"
    return f"{title[:24]}…" if len(title) > 24 else title


conversation_store = ConversationStore()
