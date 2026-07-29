"""Seed a fictional employee directory and run XiaoYuan in an isolated sandbox."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Protocol


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SANDBOX_DB_PATH = PROJECT_DIR / "data" / "sandbox.db"

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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="初始化虚构员工数据并运行小原 AI 沙箱。",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_SANDBOX_DB_PATH),
        help="沙箱 SQLite 路径，默认 data/sandbox.db。",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="只初始化并检查数据，不启动 Web 服务。",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = PROJECT_DIR / db_path
    db_path = db_path.resolve()
    database_existed = db_path.exists()

    # Shared stores resolve their default path at import time.
    os.environ["XIAOYUAN_DB_PATH"] = str(db_path)
    os.environ["XIAOYUAN_SANDBOX"] = "1"
    os.chdir(PROJECT_DIR)

    from meeting_room_tool import MeetingRoomStore
    from people_tool import PeopleStore

    people_store = PeopleStore()
    meeting_room_store = MeetingRoomStore()
    existing_people = people_store.list_all()
    if database_existed:
        print(f"已保留沙箱中的 {len(existing_people)} 条员工数据")
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
    meeting_room_count = sum(
        len(meeting_room_store.list_rooms(floor=floor)["rooms"])
        for floor in ("6", "7", "8")
    )
    print(f"已验证 {meeting_room_count} 间虚构会议室及当日日程")
    print(f"沙箱数据库：{db_path}")
    if args.seed_only:
        return

    import uvicorn

    print(f"沙箱地址：http://{args.host}:{args.port}")
    uvicorn.run("server:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
