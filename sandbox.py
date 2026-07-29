"""Enable the employee sandbox UI while using XiaoYuan's shared database."""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_XIAOYUAN_DB_PATH = PROJECT_DIR / "data" / "xiaoyuan.db"
LEGACY_SANDBOX_DB_PATH = PROJECT_DIR / "data" / "sandbox.db"
LEGACY_MEETING_ROOM_DB_PATH = PROJECT_DIR / "data" / "meeting-room-demo.db"
LEGACY_MIGRATION_KEY = "legacy_sandbox_database_merged_v1"
LEGACY_MEETING_ROOM_MIGRATION_KEY = "legacy_meeting_room_database_merged_v1"

SANDBOX_PEOPLE = (
    {
        "employee_id": "XY-S001",
        "name": "张三",
        "phone": "13800000001",
        "department": "研发部",
    },
    {
        "employee_id": "XY-S002",
        "name": "张三",
        "phone": "13800000002",
        "department": "财务部",
    },
    {
        "employee_id": "XY-S003",
        "name": "李四",
        "phone": "13800000003",
        "department": "研发部",
    },
    {
        "employee_id": "XY-S004",
        "name": "王芳",
        "phone": "13800000004",
        "department": "人力资源部",
    },
    {
        "employee_id": "XY-S005",
        "name": "赵六",
        "phone": "13800000005",
        "department": "市场部",
    },
    {
        "employee_id": "XY-S006",
        "name": "陈晨",
        "phone": "13800000006",
        "department": "产品部",
    },
    {
        "employee_id": "XY-S007",
        "name": "陈晨",
        "phone": "13800000007",
        "department": "研发部",
    },
    {
        "employee_id": "XY-S008",
        "name": "刘洋",
        "phone": "13800000008",
        "department": "法务部",
    },
    {
        "employee_id": "XY-S009",
        "name": "孙悦",
        "phone": "13800000009",
        "department": "行政部",
    },
    {
        "employee_id": "XY-S010",
        "name": "周宁",
        "phone": "13800000010",
        "department": "客户成功部",
    },
)


class PeopleWriter(Protocol):
    def upsert(
        self,
        *,
        employee_id: str,
        name: str,
        phone: str,
        department: str,
    ) -> None: ...


def seed_sandbox_people(store: PeopleWriter) -> int:
    """Idempotently seed all fictional people and return the seeded row count."""
    for person in SANDBOX_PEOPLE:
        store.upsert(**person)
    return len(SANDBOX_PEOPLE)


CORE_LEGACY_TABLE_SPECS = (
    (
        "sessions",
        ("session_id", "title", "round_count", "created_at", "updated_at"),
        ("session_id",),
        None,
    ),
    (
        "conversation_rounds",
        (
            "session_id",
            "round_no",
            "status",
            "error",
            "created_at",
            "completed_at",
        ),
        ("session_id", "round_no"),
        None,
    ),
    (
        "chat_messages",
        ("session_id", "round_no", "role", "content", "created_at"),
        ("session_id", "round_no", "role"),
        None,
    ),
    (
        "conversation_summaries",
        ("session_id", "content", "start_round", "end_round", "created_at"),
        ("session_id", "end_round"),
        None,
    ),
    (
        "model_call_audits",
        (
            "session_id",
            "round_no",
            "model_id",
            "status",
            "provider_responses_json",
            "langchain_ai_message_json",
            "error",
            "created_at",
        ),
        ("session_id", "round_no"),
        None,
    ),
    (
        "people",
        ("employee_id", "name", "phone", "department"),
        ("employee_id",),
        None,
    ),
)

MEETING_ROOM_LEGACY_TABLE_SPECS = (
    (
        "meeting_rooms",
        (
            "room_id",
            "room_name",
            "floor",
            "capacity",
            "equipment_json",
        ),
        ("room_id",),
        None,
    ),
    (
        "meeting_room_bookings",
        (
            "booking_id",
            "meeting_id",
            "room_id",
            "booking_date",
            "start_time",
            "end_time",
            "capacity",
            "theme",
            "booked_by",
            "source",
            "created_at",
        ),
        ("booking_id",),
        (
            "booking_id",
            "meeting_id",
            "room_id",
            "booking_date",
            "start_time",
            "end_time",
            "capacity",
            "theme",
            "booked_by",
            "source",
        ),
    ),
)


def _migrate_legacy_database(
    target_path: Path | str,
    source_path: Path | str,
    *,
    migration_key: str,
    table_specs,
) -> dict[str, int]:
    """Merge selected legacy tables into the shared database exactly once."""
    target = Path(target_path).expanduser().resolve()
    source = Path(source_path).expanduser().resolve()
    if source == target or not source.exists():
        return {}

    migrated: dict[str, int] = {}
    connection = sqlite3.connect(target, timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        if connection.execute(
            "SELECT 1 FROM app_metadata WHERE key = ?",
            (migration_key,),
        ).fetchone():
            return {}

        connection.execute("ATTACH DATABASE ? AS legacy", (str(source),))
        source_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM legacy.sqlite_master WHERE type = 'table'"
            )
        }
        connection.execute("BEGIN IMMEDIATE")
        for table, columns, key_columns, comparison_columns in table_specs:
            if table not in source_tables:
                continue
            column_list = ", ".join(columns)
            before = connection.total_changes
            connection.execute(
                f"""
                INSERT OR IGNORE INTO main.{table} ({column_list})
                SELECT {column_list} FROM legacy.{table}
                """
            )
            migrated[table] = connection.total_changes - before

            join_clause = " AND ".join(
                f"target.{column} = source.{column}" for column in key_columns
            )
            checked_columns = comparison_columns or columns
            mismatch_clause = " OR ".join(
                f"target.{column} IS NOT source.{column}"
                for column in checked_columns
            )
            mismatch_count = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM legacy.{table} AS source
                LEFT JOIN main.{table} AS target ON {join_clause}
                WHERE target.{key_columns[0]} IS NULL OR {mismatch_clause}
                """
            ).fetchone()[0]
            if mismatch_count:
                raise RuntimeError(
                    f"旧沙箱数据库合并冲突：{table} 有 "
                    f"{mismatch_count} 条记录无法安全合并"
                )

        connection.execute(
            "INSERT INTO app_metadata(key, value) VALUES (?, ?)",
            (migration_key, str(source)),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        try:
            connection.execute("DETACH DATABASE legacy")
        except sqlite3.OperationalError:
            pass
        connection.close()
    return migrated


def migrate_legacy_sandbox_database(
    target_path: Path | str,
    source_path: Path | str = LEGACY_SANDBOX_DB_PATH,
) -> dict[str, int]:
    """Merge the old isolated sandbox database into the shared database once."""
    return _migrate_legacy_database(
        target_path,
        source_path,
        migration_key=LEGACY_MIGRATION_KEY,
        table_specs=(
            *CORE_LEGACY_TABLE_SPECS,
            *MEETING_ROOM_LEGACY_TABLE_SPECS,
        ),
    )


def migrate_legacy_meeting_room_database(
    target_path: Path | str,
    source_path: Path | str = LEGACY_MEETING_ROOM_DB_PATH,
) -> dict[str, int]:
    """Merge the standalone meeting-room database into XiaoYuan once."""
    return _migrate_legacy_database(
        target_path,
        source_path,
        migration_key=LEGACY_MEETING_ROOM_MIGRATION_KEY,
        table_specs=MEETING_ROOM_LEGACY_TABLE_SPECS,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="使用统一数据库启动小原 AI 员工沙箱入口。",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="只初始化并检查数据，不启动 Web 服务。",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()

    load_dotenv(PROJECT_DIR / ".env")
    configured_db_path = os.getenv("XIAOYUAN_DB_PATH")
    db_path = Path(configured_db_path or DEFAULT_XIAOYUAN_DB_PATH).expanduser()
    if not db_path.is_absolute():
        db_path = PROJECT_DIR / db_path
    db_path = db_path.resolve()
    database_existed = db_path.exists()

    # Sandbox mode only controls access to its page and API. It does not select
    # a separate database.
    os.environ["XIAOYUAN_SANDBOX"] = "1"
    os.chdir(PROJECT_DIR)

    from conversation_store import ConversationStore
    from meeting_room_tool import MeetingRoomStore
    from people_tool import PeopleStore

    ConversationStore(db_path)
    people_store = PeopleStore(db_path)
    meeting_room_store = MeetingRoomStore(
        db_path,
        seed_sandbox_data=False,
    )
    if db_path == DEFAULT_XIAOYUAN_DB_PATH:
        legacy_sources = (
            (
                "旧 sandbox.db",
                migrate_legacy_sandbox_database(db_path),
            ),
            (
                "旧 meeting-room-demo.db",
                migrate_legacy_meeting_room_database(db_path),
            ),
        )
        for label, migrated in legacy_sources:
            if not migrated:
                continue
            details = "，".join(
                f"{table} {count} 条"
                for table, count in migrated.items()
                if count
            )
            print(
                f"已将 {label} 合并到统一数据库："
                f"{details or '无新增记录'}"
            )
    existing_people = people_store.list_all()
    if database_existed:
        print(f"已保留统一数据库中的 {len(existing_people)} 条员工数据")
    else:
        seeded_count = seed_sandbox_people(people_store)
        checks = {
            person["employee_id"]: people_store.find(
                employee_id=person["employee_id"]
            )["status"]
            for person in SANDBOX_PEOPLE
        }
        if any(status != "found" for status in checks.values()):
            raise RuntimeError(f"沙箱员工数据校验失败：{checks}")
        print(f"已写入并验证 {seeded_count} 条虚构员工数据")
    meeting_room_store.seed_sandbox_data()
    meeting_room_count = sum(
        len(meeting_room_store.list_rooms(floor=floor)["rooms"])
        for floor in ("6", "7", "8")
    )
    print(f"已验证 {meeting_room_count} 间虚构会议室及当日日程")
    print(f"统一数据库：{db_path}")
    if args.seed_only:
        return

    import uvicorn

    print(f"沙箱地址：http://{args.host}:{args.port}")
    uvicorn.run("server:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
