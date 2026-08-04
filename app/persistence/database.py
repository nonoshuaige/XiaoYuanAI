"""Database configuration, MySQL pooling, and dialect compatibility helpers."""

from __future__ import annotations

import os
import queue
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from dotenv import load_dotenv

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ModuleNotFoundError:  # Legacy SQLite-only maintenance commands.
    pymysql = None
    DictCursor = None


PROJECT_DIR = Path(__file__).resolve().parent
MYSQL_MIGRATIONS_DIR = PROJECT_DIR / "migrations" / "mysql"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
_NAMED_PARAMETER_PATTERN = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")

load_dotenv()


@dataclass(frozen=True)
class MySQLSettings:
    host: str
    port: int
    user: str
    password: str
    database: str
    pool_size: int
    connect_timeout: int

    @classmethod
    def from_env(cls) -> "MySQLSettings":
        settings = cls(
            host=os.getenv("XIAOYUAN_MYSQL_HOST", "127.0.0.1").strip(),
            port=_env_int("XIAOYUAN_MYSQL_PORT", 3306, minimum=1, maximum=65535),
            user=os.getenv("XIAOYUAN_MYSQL_USER", "root").strip(),
            password=os.getenv("XIAOYUAN_MYSQL_PASSWORD", ""),
            database=os.getenv("XIAOYUAN_MYSQL_DATABASE", "xiaoyuan_ai").strip(),
            pool_size=_env_int("XIAOYUAN_MYSQL_POOL_SIZE", 10, minimum=1, maximum=100),
            connect_timeout=_env_int(
                "XIAOYUAN_MYSQL_CONNECT_TIMEOUT", 5, minimum=1, maximum=60
            ),
        )
        if not settings.host:
            raise RuntimeError("XIAOYUAN_MYSQL_HOST 不能为空")
        if not settings.user:
            raise RuntimeError("XIAOYUAN_MYSQL_USER 不能为空")
        if not _IDENTIFIER_PATTERN.fullmatch(settings.database):
            raise RuntimeError(
                "XIAOYUAN_MYSQL_DATABASE 只能包含字母、数字和下划线"
            )
        return settings

    @property
    def display_name(self) -> str:
        return f"mysql://{self.user}@{self.host}:{self.port}/{self.database}"


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是整数") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return value


MYSQL_SETTINGS = MySQLSettings.from_env()
DatabaseIntegrityError = (
    (sqlite3.IntegrityError, pymysql.IntegrityError)
    if pymysql is not None
    else (sqlite3.IntegrityError,)
)


class MySQLCursor:
    """Small DB-API cursor facade returning dictionary-shaped rows."""

    def __init__(self, cursor: Any):
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    @property
    def lastrowid(self) -> int:
        return int(self._cursor.lastrowid or 0)

    def fetchone(self) -> dict[str, Any] | None:
        return self._cursor.fetchone()

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._cursor.fetchall())

    def __iter__(self):
        return iter(self._cursor)


class MySQLConnection:
    """Context-managed pooled connection with SQLite-like execute methods."""

    dialect = "mysql"

    def __init__(self, pool: "MySQLPool"):
        self._pool = pool
        self._connection: Any | None = None
        self._cursors: list[Any] = []

    def __enter__(self) -> "MySQLConnection":
        self._connection = self._pool.acquire()
        self._connection.begin()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self._connection is not None
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        finally:
            for cursor in self._cursors:
                cursor.close()
            self._cursors.clear()
            self._pool.release(self._connection)
            self._connection = None

    def execute(
        self,
        sql: str,
        parameters: Mapping[str, Any] | Sequence[Any] | None = None,
    ) -> MySQLCursor:
        if self._connection is None:
            raise RuntimeError("数据库连接尚未进入上下文")
        normalized = sql.strip().rstrip(";")
        if normalized.upper() == "BEGIN IMMEDIATE":
            cursor = self._connection.cursor(DictCursor)
            self._cursors.append(cursor)
            cursor.execute("SELECT 1")
            return MySQLCursor(cursor)
        cursor = self._connection.cursor(DictCursor)
        self._cursors.append(cursor)
        cursor.execute(_mysql_sql(sql, parameters), parameters or ())
        return MySQLCursor(cursor)

    def executemany(
        self,
        sql: str,
        parameter_rows: Iterable[Mapping[str, Any] | Sequence[Any]],
    ) -> MySQLCursor:
        if self._connection is None:
            raise RuntimeError("数据库连接尚未进入上下文")
        rows = list(parameter_rows)
        cursor = self._connection.cursor(DictCursor)
        self._cursors.append(cursor)
        if rows:
            cursor.executemany(_mysql_sql(sql, rows[0]), rows)
        return MySQLCursor(cursor)


def _mysql_sql(
    sql: str,
    parameters: Mapping[str, Any] | Sequence[Any] | None,
) -> str:
    if isinstance(parameters, Mapping):
        return _NAMED_PARAMETER_PATTERN.sub(r"%(\1)s", sql)
    return sql.replace("?", "%s")


class MySQLPool:
    def __init__(self, settings: MySQLSettings):
        self.settings = settings
        self._available: queue.LifoQueue[Any] = queue.LifoQueue(
            maxsize=settings.pool_size
        )
        self._created = 0
        self._guard = threading.Lock()

    def _new_connection(self):
        _require_pymysql()
        return pymysql.connect(
            host=self.settings.host,
            port=self.settings.port,
            user=self.settings.user,
            password=self.settings.password,
            database=self.settings.database,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
            connect_timeout=self.settings.connect_timeout,
            read_timeout=30,
            write_timeout=30,
        )

    def acquire(self):
        try:
            connection = self._available.get_nowait()
        except queue.Empty:
            with self._guard:
                if self._created < self.settings.pool_size:
                    connection = self._new_connection()
                    self._created += 1
                else:
                    connection = self._available.get(timeout=10)
        try:
            connection.ping()
            return connection
        except pymysql.Error:
            connection.close()
            with self._guard:
                self._created = max(0, self._created - 1)
            return self.acquire()

    def release(self, connection) -> None:
        try:
            self._available.put_nowait(connection)
        except queue.Full:
            connection.close()
            with self._guard:
                self._created = max(0, self._created - 1)

    def connection(self) -> MySQLConnection:
        return MySQLConnection(self)


_pool: MySQLPool | None = None
_pool_lock = threading.Lock()
_schema_ready = False
_schema_lock = threading.Lock()


def ensure_mysql_schema() -> None:
    """Create the configured database and apply versioned SQL migrations."""
    global _schema_ready
    _require_pymysql()
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        settings = MYSQL_SETTINGS
        bootstrap = pymysql.connect(
            host=settings.host,
            port=settings.port,
            user=settings.user,
            password=settings.password,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=settings.connect_timeout,
        )
        try:
            with bootstrap.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{settings.database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
        finally:
            bootstrap.close()

        connection = pymysql.connect(
            host=settings.host,
            port=settings.port,
            user=settings.user,
            password=settings.password,
            database=settings.database,
            charset="utf8mb4",
            autocommit=False,
            connect_timeout=settings.connect_timeout,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version VARCHAR(128) PRIMARY KEY,
                        applied_at VARCHAR(40) NOT NULL
                    ) ENGINE=InnoDB
                    """
                )
                cursor.execute("SELECT version FROM schema_migrations")
                applied = {row[0] for row in cursor.fetchall()}
                for migration_path in sorted(MYSQL_MIGRATIONS_DIR.glob("*.sql")):
                    if migration_path.name in applied:
                        continue
                    statements = _split_sql_script(
                        migration_path.read_text(encoding="utf-8")
                    )
                    for statement in statements:
                        cursor.execute(statement)
                    cursor.execute(
                        "INSERT INTO schema_migrations(version, applied_at) "
                        "VALUES (%s, %s)",
                        (migration_path.name, datetime.now().isoformat()),
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        _schema_ready = True


def _split_sql_script(script: str) -> list[str]:
    lines = [line for line in script.splitlines() if not line.lstrip().startswith("--")]
    return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]


def mysql_connection() -> MySQLConnection:
    global _pool
    ensure_mysql_schema()
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = MySQLPool(MYSQL_SETTINGS)
    return _pool.connection()


def _require_pymysql() -> None:
    if pymysql is None:
        raise RuntimeError(
            "MySQL 驱动未安装，请先运行 pip install -r requirements.txt"
        )


def sqlite_connection(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def is_mysql_target(db_path: Path | str | None) -> bool:
    return db_path is None


def database_display_name(db_path: Path | str | None) -> str:
    return MYSQL_SETTINGS.display_name if db_path is None else str(Path(db_path))


def mysql_health() -> dict[str, str]:
    with mysql_connection() as connection:
        row = connection.execute(
            "SELECT VERSION() AS version, DATABASE() AS database_name"
        ).fetchone()
    return {
        "status": "ok",
        "database": str(row["database_name"]),
        "version": str(row["version"]),
    }
