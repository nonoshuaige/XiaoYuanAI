"""MySQL persistence for complete conversations and rolling summaries."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.persistence.database import (
    database_display_name,
    ensure_mysql_schema,
    is_mysql_target,
    mysql_connection,
    sqlite_connection,
)


PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_DIR / "data"
_configured_db_path = os.getenv("XIAOYUAN_DB_PATH")
DEFAULT_DB_PATH = (
    (
        Path(_configured_db_path).expanduser()
        if Path(_configured_db_path).expanduser().is_absolute()
        else PROJECT_DIR / _configured_db_path
    )
    if _configured_db_path
    else DATA_DIR / "xiaoyuan.db"
)
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ConversationSummary:
    session_id: str
    content: str
    start_round: int
    end_round: int
    created_at: str


class ConversationPendingError(RuntimeError):
    """Raised when a session already has an unfinished model turn."""

    def __init__(self, round_no: int):
        self.round_no = round_no
        super().__init__(f"第 {round_no} 轮仍在生成，请等待完成后再发送")


class ConversationStore:
    """Keep the full transcript and cumulative summary versions in MySQL."""

    def __init__(self, db_path: Path | str | None = None):
        if db_path is None and _configured_db_path:
            db_path = DEFAULT_DB_PATH
        self._mysql = is_mysql_target(db_path)
        self._sqlite_path = Path(db_path) if db_path is not None else None
        self.db_path = database_display_name(db_path)
        self._write_lock = threading.RLock()
        self._init_schema()

    @staticmethod
    def validate_session_id(session_id: str) -> str:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError(
                "sessionId 只能包含字母、数字、下划线和短横线，长度不超过 64"
            )
        return session_id

    def _connect(self) -> Any:
        if self._mysql:
            return mysql_connection()
        assert self._sqlite_path is not None
        return sqlite_connection(self._sqlite_path)

    def _init_schema(self) -> None:
        if self._mysql:
            ensure_mysql_schema()
            return
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

                CREATE TABLE IF NOT EXISTS conversation_rounds (
                    session_id TEXT NOT NULL,
                    round_no INTEGER NOT NULL,
                    model_id TEXT,
                    context_window_tokens INTEGER NOT NULL DEFAULT 16384,
                    context_estimated_tokens INTEGER,
                    context_truncated INTEGER NOT NULL DEFAULT 0,
                    context_dropped_rounds INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    token_usage_estimated INTEGER NOT NULL DEFAULT 0,
                    job_owner TEXT NOT NULL DEFAULT 'default',
                    status TEXT NOT NULL
                        CHECK (status IN ('pending', 'completed', 'failed')),
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY (session_id, round_no),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_rounds_session_status
                ON conversation_rounds(session_id, status, round_no);

                CREATE TABLE IF NOT EXISTS model_call_audits (
                    session_id TEXT NOT NULL,
                    round_no INTEGER NOT NULL,
                    provider_responses_json TEXT NOT NULL DEFAULT '[]',
                    langchain_ai_message_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, round_no),
                    FOREIGN KEY (session_id, round_no)
                        REFERENCES conversation_rounds(session_id, round_no)
                        ON DELETE CASCADE
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
            message_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(chat_messages)"
                )
            }
            if "quick_replies_json" in message_columns:
                connection.execute(
                    "ALTER TABLE chat_messages DROP COLUMN quick_replies_json"
                )
            round_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(conversation_rounds)"
                )
            }
            if "model_id" not in round_columns:
                connection.execute(
                    """
                    ALTER TABLE conversation_rounds
                    ADD COLUMN model_id TEXT
                    """
                )
            if "job_owner" not in round_columns:
                connection.execute(
                    """
                    ALTER TABLE conversation_rounds
                    ADD COLUMN job_owner TEXT NOT NULL DEFAULT 'default'
                    """
                )
            round_column_definitions = {
                "context_window_tokens": (
                    "INTEGER NOT NULL DEFAULT 16384"
                ),
                "context_estimated_tokens": "INTEGER",
                "context_truncated": "INTEGER NOT NULL DEFAULT 0",
                "context_dropped_rounds": "INTEGER NOT NULL DEFAULT 0",
                "input_tokens": "INTEGER",
                "output_tokens": "INTEGER",
                "total_tokens": "INTEGER",
                "token_usage_estimated": "INTEGER NOT NULL DEFAULT 0",
            }
            for column_name, definition in round_column_definitions.items():
                if column_name not in round_columns:
                    connection.execute(
                        f"ALTER TABLE conversation_rounds "
                        f"ADD COLUMN {column_name} {definition}"
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
            if schema_version < 3:
                connection.execute(
                    """
                    INSERT INTO conversation_rounds(
                        session_id,
                        round_no,
                        status,
                        error,
                        created_at,
                        completed_at
                    )
                    SELECT
                        m.session_id,
                        m.round_no,
                        CASE
                            WHEN SUM(
                                CASE WHEN m.role = 'assistant' THEN 1 ELSE 0 END
                            ) > 0
                            THEN 'completed'
                            ELSE 'failed'
                        END,
                        CASE
                            WHEN SUM(
                                CASE WHEN m.role = 'assistant' THEN 1 ELSE 0 END
                            ) > 0
                            THEN NULL
                            ELSE '历史轮次缺少助手回复'
                        END,
                        MIN(m.created_at),
                        CASE
                            WHEN SUM(
                                CASE WHEN m.role = 'assistant' THEN 1 ELSE 0 END
                            ) > 0
                            THEN MAX(m.created_at)
                            ELSE NULL
                        END
                    FROM chat_messages AS m
                    GROUP BY m.session_id, m.round_no
                    ON CONFLICT(session_id, round_no) DO NOTHING
                    """
                )
                connection.execute("PRAGMA user_version = 3")
            if schema_version < 4:
                connection.execute("PRAGMA user_version = 4")
            if schema_version < 5:
                connection.execute("PRAGMA user_version = 5")
            if schema_version < 6:
                connection.execute("PRAGMA user_version = 6")
            if schema_version < 7:
                connection.execute("PRAGMA user_version = 7")

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

    def begin_round(
        self,
        session_id: str,
        user_content: str,
        *,
        model_id: str | None = None,
        job_owner: str = "default",
        context_window_tokens: int = 16_384,
    ) -> int:
        """Persist the user message before any model request starts."""
        session_id = self.validate_session_id(session_id)
        now = datetime.now().isoformat()
        with self._write_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                (
                    """
                    INSERT INTO sessions(
                        session_id, title, round_count, created_at, updated_at
                    )
                    VALUES (?, '新对话', 0, ?, ?)
                    ON DUPLICATE KEY UPDATE session_id = VALUES(session_id)
                    """
                    if self._mysql
                    else """
                    INSERT INTO sessions(
                        session_id, title, round_count, created_at, updated_at
                    )
                    VALUES (?, '新对话', 0, ?, ?)
                    ON CONFLICT(session_id) DO NOTHING
                    """
                ),
                (session_id, now, now),
            )
            row = connection.execute(
                f"""
                SELECT round_count + 1 AS next_round
                FROM sessions
                WHERE session_id = ?
                {"FOR UPDATE" if self._mysql else ""}
                """,
                (session_id,),
            ).fetchone()
            pending = connection.execute(
                """
                SELECT round_no
                FROM conversation_rounds
                WHERE session_id = ? AND status = 'pending'
                ORDER BY round_no
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if pending is not None:
                raise ConversationPendingError(int(pending["round_no"]))
            round_no = int(row["next_round"])
            connection.execute(
                """
                INSERT INTO conversation_rounds(
                    session_id,
                    round_no,
                    model_id,
                    context_window_tokens,
                    job_owner,
                    status,
                    error,
                    created_at,
                    completed_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?, NULL)
                """,
                (
                    session_id,
                    round_no,
                    model_id,
                    context_window_tokens,
                    job_owner,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO chat_messages(
                    session_id, round_no, role, content, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, round_no, "user", user_content, now),
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

    def complete_round(
        self,
        session_id: str,
        round_no: int,
        assistant_content: str,
        *,
        model_call: dict[str, Any] | None = None,
        token_usage: dict[str, int] | None = None,
        context_estimated_tokens: int | None = None,
        context_truncated: bool = False,
        context_dropped_rounds: int = 0,
        token_usage_estimated: bool = False,
    ) -> None:
        """Append the assistant reply and close a previously pending round."""
        session_id = self.validate_session_id(session_id)
        now = datetime.now().isoformat()
        with self._write_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status
                FROM conversation_rounds
                WHERE session_id = ? AND round_no = ?
                """,
                (session_id, round_no),
            ).fetchone()
            if row is None:
                raise RuntimeError("待完成的对话轮次不存在")
            if row["status"] != "pending":
                raise RuntimeError(
                    f"对话轮次无法完成，当前状态为 {row['status']}"
                )
            connection.execute(
                """
                INSERT INTO chat_messages(
                    session_id,
                    round_no,
                    role,
                    content,
                    created_at
                ) VALUES (?, ?, 'assistant', ?, ?)
                """,
                (
                    session_id,
                    round_no,
                    assistant_content,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE conversation_rounds
                SET
                    status = 'completed',
                    error = NULL,
                    context_estimated_tokens = ?,
                    context_truncated = ?,
                    context_dropped_rounds = ?,
                    input_tokens = ?,
                    output_tokens = ?,
                    total_tokens = ?,
                    token_usage_estimated = ?,
                    completed_at = ?
                WHERE session_id = ? AND round_no = ?
                """,
                (
                    context_estimated_tokens,
                    int(context_truncated),
                    context_dropped_rounds,
                    token_usage.get("input_tokens") if token_usage else None,
                    token_usage.get("output_tokens") if token_usage else None,
                    token_usage.get("total_tokens") if token_usage else None,
                    int(token_usage_estimated),
                    now,
                    session_id,
                    round_no,
                ),
            )
            if model_call is not None:
                self._write_model_call(
                    connection,
                    session_id=session_id,
                    round_no=round_no,
                    model_call=model_call,
                    created_at=now,
                )
            connection.execute(
                """
                UPDATE sessions
                SET updated_at = ?
                WHERE session_id = ?
                """,
                (now, session_id),
            )

    def fail_round(
        self,
        session_id: str,
        round_no: int,
        error: str,
        *,
        model_call: dict[str, Any] | None = None,
    ) -> bool:
        """Keep the user message and mark the unfinished model turn as failed."""
        session_id = self.validate_session_id(session_id)
        now = datetime.now().isoformat()
        error_text = error.strip()[:2_000] or "模型请求失败"
        with self._write_lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE conversation_rounds
                SET
                    status = 'failed',
                    error = ?,
                    completed_at = ?
                WHERE
                    session_id = ?
                    AND round_no = ?
                    AND status = 'pending'
                """,
                (error_text, now, session_id, round_no),
            )
            if cursor.rowcount:
                if model_call is not None:
                    self._write_model_call(
                        connection,
                        session_id=session_id,
                        round_no=round_no,
                        model_call=model_call,
                        created_at=now,
                    )
                connection.execute(
                    """
                    UPDATE sessions
                    SET updated_at = ?
                    WHERE session_id = ?
                    """,
                    (now, session_id),
                )
        return cursor.rowcount > 0

    def get_model_calls(
        self,
        session_id: str,
        *,
        round_no: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return complete Provider and LangChain records for model calls."""
        session_id = self.validate_session_id(session_id)
        query = """
            SELECT
                a.session_id,
                a.round_no,
                r.model_id,
                r.status,
                a.provider_responses_json,
                a.langchain_ai_message_json,
                r.error,
                a.created_at
            FROM model_call_audits AS a
            JOIN conversation_rounds AS r
                ON r.session_id = a.session_id
                AND r.round_no = a.round_no
            WHERE a.session_id = ?
        """
        parameters: list[Any] = [session_id]
        if round_no is not None:
            if round_no < 1:
                raise ValueError("round_no 必须大于 0")
            query += " AND a.round_no = ?"
            parameters.append(round_no)
        query += " ORDER BY a.round_no"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                "sessionId": row["session_id"],
                "round": int(row["round_no"]),
                "modelId": row["model_id"],
                "status": row["status"],
                "providerResponses": json.loads(
                    row["provider_responses_json"]
                ),
                "langchainAIMessage": (
                    json.loads(row["langchain_ai_message_json"])
                    if row["langchain_ai_message_json"] is not None
                    else None
                ),
                "error": row["error"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def append_round(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
    ) -> int:
        """Compatibility helper for atomically observable completed rounds."""
        round_no = self.begin_round(session_id, user_content)
        self.complete_round(session_id, round_no, assistant_content)
        return round_no

    def get_messages(
        self,
        session_id: str,
        *,
        after_round: int = 0,
        through_round: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read stored messages in stable user-then-assistant order."""
        session_id = self.validate_session_id(session_id)
        query = """
            SELECT
                m.round_no,
                m.role,
                m.content,
                m.created_at,
                COALESCE(r.status, 'completed') AS status,
                r.error,
                r.context_window_tokens,
                r.context_estimated_tokens,
                r.context_truncated,
                r.context_dropped_rounds,
                r.input_tokens,
                r.output_tokens,
                r.total_tokens,
                r.token_usage_estimated
            FROM chat_messages AS m
            LEFT JOIN conversation_rounds AS r
                ON r.session_id = m.session_id
                AND r.round_no = m.round_no
            WHERE m.session_id = ? AND m.round_no > ?
        """
        parameters: list[Any] = [session_id, after_round]
        if through_round is not None:
            query += " AND m.round_no <= ?"
            parameters.append(through_round)
        query += """
            ORDER BY
                m.round_no,
                CASE m.role WHEN 'user' THEN 0 ELSE 1 END
        """
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                "round": row["round_no"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
                "status": row["status"],
                "error": row["error"],
                "contextWindowTokens": row["context_window_tokens"],
                "contextEstimatedTokens": row["context_estimated_tokens"],
                "contextTruncated": bool(row["context_truncated"]),
                "contextDroppedRounds": int(
                    row["context_dropped_rounds"] or 0
                ),
                "inputTokens": row["input_tokens"],
                "outputTokens": row["output_tokens"],
                "totalTokens": row["total_tokens"],
                "tokenUsageEstimated": bool(
                    row["token_usage_estimated"]
                ),
            }
            for row in rows
        ]

    def latest_round(self, session_id: str) -> int:
        session_id = self.validate_session_id(session_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT round_count AS latest_round
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return int(row["latest_round"]) if row else 0

    def get_round(
        self,
        session_id: str,
        round_no: int,
    ) -> dict[str, Any] | None:
        session_id = self.validate_session_id(session_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    round_no,
                    model_id,
                    context_window_tokens,
                    context_estimated_tokens,
                    context_truncated,
                    context_dropped_rounds,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    token_usage_estimated,
                    job_owner,
                    status,
                    error,
                    created_at,
                    completed_at
                FROM conversation_rounds
                WHERE session_id = ? AND round_no = ?
                """,
                (session_id, round_no),
            ).fetchone()
        if row is None:
            return None
        return {
            "round": int(row["round_no"]),
            "modelId": row["model_id"],
            "contextWindowTokens": int(row["context_window_tokens"]),
            "contextEstimatedTokens": row["context_estimated_tokens"],
            "contextTruncated": bool(row["context_truncated"]),
            "contextDroppedRounds": int(row["context_dropped_rounds"] or 0),
            "inputTokens": row["input_tokens"],
            "outputTokens": row["output_tokens"],
            "totalTokens": row["total_tokens"],
            "tokenUsageEstimated": bool(row["token_usage_estimated"]),
            "jobOwner": row["job_owner"],
            "status": row["status"],
            "error": row["error"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        }

    def get_round_user_message(
        self,
        session_id: str,
        round_no: int,
    ) -> str | None:
        """Return the durable user input for one model turn."""
        session_id = self.validate_session_id(session_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT content
                FROM chat_messages
                WHERE
                    session_id = ?
                    AND round_no = ?
                    AND role = 'user'
                """,
                (session_id, round_no),
            ).fetchone()
        return str(row["content"]) if row is not None else None

    def list_pending_rounds(
        self,
        *,
        job_owner: str | None = None,
    ) -> list[dict[str, Any]]:
        """List durable model turns that need a background worker."""
        query = """
            SELECT
                r.session_id,
                r.round_no,
                r.model_id,
                r.job_owner,
                r.created_at,
                m.content AS user_content
            FROM conversation_rounds AS r
            JOIN chat_messages AS m
                ON m.session_id = r.session_id
                AND m.round_no = r.round_no
                AND m.role = 'user'
            WHERE r.status = 'pending'
        """
        parameters: tuple[str, ...] = ()
        if job_owner is not None:
            query += " AND r.job_owner = ?"
            parameters = (job_owner,)
        query += " ORDER BY r.created_at, r.session_id, r.round_no"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                "sessionId": row["session_id"],
                "round": int(row["round_no"]),
                "modelId": row["model_id"],
                "jobOwner": row["job_owner"],
                "userContent": row["user_content"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

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

    def _write_model_call(
        self,
        connection: Any,
        *,
        session_id: str,
        round_no: int,
        model_call: dict[str, Any],
        created_at: str,
    ) -> None:
        connection.execute(
            (
                """
                INSERT INTO model_call_audits(
                    session_id,
                    round_no,
                    provider_responses_json,
                    langchain_ai_message_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE
                    provider_responses_json = VALUES(provider_responses_json),
                    langchain_ai_message_json = VALUES(langchain_ai_message_json),
                    created_at = VALUES(created_at)
                """
                if self._mysql
                else """
            INSERT INTO model_call_audits(
                session_id,
                round_no,
                provider_responses_json,
                langchain_ai_message_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id, round_no) DO UPDATE SET
                provider_responses_json = excluded.provider_responses_json,
                langchain_ai_message_json =
                    excluded.langchain_ai_message_json,
                created_at = excluded.created_at
            """
            ),
            (
                session_id,
                round_no,
                json.dumps(
                    model_call.get("provider_responses", []),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                (
                    json.dumps(
                        model_call["langchain_ai_message"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if model_call.get("langchain_ai_message") is not None
                    else None
                ),
                created_at,
            ),
        )

    @staticmethod
    def _session_row(row: Any) -> dict[str, Any]:
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
