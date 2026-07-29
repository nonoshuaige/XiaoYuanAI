"""Employee directory persistence and the ``find_person`` agent tool."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, model_validator

from conversation_store import DEFAULT_DB_PATH


class DuplicatePersonError(ValueError):
    """A create or update conflicts with an existing employee."""


class PersonNotFoundError(LookupError):
    """The requested employee ID does not exist."""


class FindPersonInput(BaseModel):
    """Validated clues extracted from the user's natural-language request."""

    employee_id: str | None = Field(
        default=None,
        description="用户提供的工号，例如 E1024；未提供时留空。",
    )
    phone: str | None = Field(
        default=None,
        description="用户提供的手机号；未提供时留空。",
    )
    name: str | None = Field(
        default=None,
        description="用户提供的姓名；未提供时留空。",
    )
    department: str | None = Field(
        default=None,
        description="用户提供的辅助部门线索；未提供时留空。",
    )

    @model_validator(mode="after")
    def normalize_and_require_identity(self) -> "FindPersonInput":
        for field_name in ("employee_id", "phone", "name", "department"):
            value = getattr(self, field_name)
            if value is not None:
                normalized = value.strip()
                setattr(self, field_name, normalized or None)
        if not any((self.employee_id, self.phone, self.name)):
            raise ValueError("工号、手机号、姓名至少需要提供一个")
        return self


class PeopleStore:
    """SQLite repository for the internal employee directory."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS people (
                    employee_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL UNIQUE,
                    department TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_people_name
                ON people(name);

                CREATE INDEX IF NOT EXISTS idx_people_department_name
                ON people(department, name);
                """
            )

    def upsert(
        self,
        *,
        employee_id: str,
        name: str,
        phone: str,
        department: str,
    ) -> None:
        """Insert or update one employee, primarily for directory synchronization."""
        values = {
            "employee_id": employee_id.strip(),
            "name": name.strip(),
            "phone": phone.strip(),
            "department": department.strip(),
        }
        if not all(values.values()):
            raise ValueError("工号、姓名、手机号、部门均不能为空")
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO people(employee_id, name, phone, department)
                VALUES (:employee_id, :name, :phone, :department)
                ON CONFLICT(employee_id) DO UPDATE SET
                    name = excluded.name,
                    phone = excluded.phone,
                    department = excluded.department
                """,
                values,
            )

    def list_all(self, search: str | None = None) -> list[dict[str, str]]:
        """List employees, optionally filtering across all visible fields."""
        normalized_search = _clean(search)
        sql = (
            "SELECT employee_id, name, phone, department "
            "FROM people"
        )
        parameters: list[str] = []
        if normalized_search:
            escaped = (
                normalized_search.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            pattern = f"%{escaped}%"
            sql += (
                " WHERE employee_id LIKE ? ESCAPE '\\' "
                "OR name LIKE ? ESCAPE '\\' "
                "OR phone LIKE ? ESCAPE '\\' "
                "OR department LIKE ? ESCAPE '\\'"
            )
            parameters.extend([pattern] * 4)
        sql += " ORDER BY employee_id LIMIT 500"
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    def create(
        self,
        *,
        employee_id: str,
        name: str,
        phone: str,
        department: str,
    ) -> dict[str, str]:
        """Create one employee and reject duplicate IDs or phone numbers."""
        values = _person_values(
            employee_id=employee_id,
            name=name,
            phone=phone,
            department=department,
        )
        try:
            with self._write_lock, self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO people(employee_id, name, phone, department)
                    VALUES (:employee_id, :name, :phone, :department)
                    """,
                    values,
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicatePersonError("工号或手机号已存在") from exc
        return values

    def update(
        self,
        current_employee_id: str,
        *,
        employee_id: str,
        name: str,
        phone: str,
        department: str,
    ) -> dict[str, str]:
        """Replace all editable fields, including the employee ID."""
        current_employee_id = current_employee_id.strip()
        values = _person_values(
            employee_id=employee_id,
            name=name,
            phone=phone,
            department=department,
        )
        try:
            with self._write_lock, self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE people
                    SET employee_id = :employee_id,
                        name = :name,
                        phone = :phone,
                        department = :department
                    WHERE employee_id = :current_employee_id
                    """,
                    {**values, "current_employee_id": current_employee_id},
                )
                if cursor.rowcount == 0:
                    raise PersonNotFoundError(
                        f"员工 {current_employee_id} 不存在"
                    )
        except sqlite3.IntegrityError as exc:
            raise DuplicatePersonError("工号或手机号已存在") from exc
        return values

    def delete(self, employee_id: str) -> None:
        """Delete one employee by exact employee ID."""
        employee_id = employee_id.strip()
        with self._write_lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM people WHERE employee_id = ?",
                (employee_id,),
            )
            if cursor.rowcount == 0:
                raise PersonNotFoundError(f"员工 {employee_id} 不存在")

    def find(
        self,
        *,
        employee_id: str | None = None,
        phone: str | None = None,
        name: str | None = None,
        department: str | None = None,
    ) -> dict[str, Any]:
        """Find employees by priority and verify every additional clue."""
        clues = {
            "employee_id": _clean(employee_id),
            "phone": _clean(phone),
            "name": _clean(name),
        }
        department = _clean(department)
        all_clues = {**clues, "department": department}
        selected_field = next(
            (field for field in ("employee_id", "phone", "name") if clues[field]),
            None,
        )
        if selected_field is None:
            return {
                "status": "invalid_input",
                "matched_by": None,
                "checked_fields": [],
                "conflicting_fields": [],
                "people": [],
                "message": "请至少提供工号、手机号或姓名中的一项。",
            }

        sql = (
            "SELECT employee_id, name, phone, department "
            f"FROM people WHERE {selected_field} = ?"
        )
        parameters: list[str] = [clues[selected_field] or ""]
        sql += " ORDER BY employee_id LIMIT 21"

        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
            alternative_matched_fields = (
                [
                    field
                    for field in ("employee_id", "phone", "name")
                    if (
                        field != selected_field
                        and clues[field]
                        and connection.execute(
                            f"SELECT 1 FROM people WHERE {field} = ? LIMIT 1",
                            (clues[field],),
                        ).fetchone()
                    )
                ]
                if not rows
                else []
            )
        primary_matches = [dict(row) for row in rows]
        additional_clues = {
            field: value
            for field, value in all_clues.items()
            if field != selected_field and value
        }
        checked_fields = [
            field
            for field in ("employee_id", "phone", "name", "department")
            if all_clues[field]
        ]

        if not primary_matches:
            people: list[dict[str, str]] = []
            if alternative_matched_fields:
                status = "conflicting_clues"
                conflicting_fields = [
                    field
                    for field in ("employee_id", "phone", "name")
                    if clues[field]
                ]
                labels = "、".join(
                    _FIELD_LABELS[field] for field in conflicting_fields
                )
                message = (
                    f"提供的{labels}线索无法指向同一位员工。请核对后重试。"
                )
            else:
                status = "not_found"
                conflicting_fields = []
                message = (
                    f"没有找到匹配的员工"
                    f"（按{_FIELD_LABELS[selected_field]}查询）。"
                )
        else:
            people = [
                person
                for person in primary_matches
                if all(
                    person[field] == value
                    for field, value in additional_clues.items()
                )
            ]
            conflicting_fields = (
                [
                    field
                    for field, value in additional_clues.items()
                    if all(
                        person[field] != value
                        for person in primary_matches
                    )
                ]
                if not people
                else []
            )

        if primary_matches and not people:
            status = "conflicting_clues"
            labels = "、".join(
                _FIELD_LABELS[field] for field in conflicting_fields
            )
            message = (
                f"提供的线索不一致：按{_FIELD_LABELS[selected_field]}"
                f"定位后，{labels or '其他线索'}与记录不符。请核对后重试。"
            )
        elif len(people) == 1:
            status = "found"
            message = (
                f"已按{_FIELD_LABELS[selected_field]}查询并验证全部线索，"
                "找到 1 位员工。"
            )
        elif len(people) > 1:
            status = "multiple_matches"
            message = (
                f"按{_FIELD_LABELS[selected_field]}查询并验证全部线索后，"
                f"找到 {len(people)} 位员工，"
                "需要结合部门或其他信息请用户确认。"
            )
        return {
            "status": status,
            "matched_by": selected_field,
            "checked_fields": checked_fields,
            "conflicting_fields": conflicting_fields,
            "people": people,
            "message": message,
        }


def create_find_person_tool(store: PeopleStore) -> BaseTool:
    """Bind one employee repository to the Agent's natural-language tool."""

    @tool(
        "find_person",
        args_schema=FindPersonInput,
        description=(
            "查询员工通讯录。仅当用户明确要求找人、确认员工或查询联系方式，"
            "并已提供工号、手机号或姓名之一时调用；满足条件就立即调用，"
            "不要因为可能重名而提前追问。没有上述身份线索时先询问，不调用。"
            "把用户明确提供的全部线索如实传入，不要猜测；工具会校验线索是否一致，"
            "冲突时不得选择某个人。部门只能辅助查询，不能单独用于找人。"
        ),
    )
    def find_person(
        employee_id: str | None = None,
        phone: str | None = None,
        name: str | None = None,
        department: str | None = None,
    ) -> dict[str, Any]:
        return store.find(
            employee_id=employee_id,
            phone=phone,
            name=name,
            department=department,
        )

    return find_person


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _person_values(
    *,
    employee_id: str,
    name: str,
    phone: str,
    department: str,
) -> dict[str, str]:
    values = {
        "employee_id": employee_id.strip(),
        "name": name.strip(),
        "phone": phone.strip(),
        "department": department.strip(),
    }
    if not all(values.values()):
        raise ValueError("工号、姓名、手机号、部门均不能为空")
    return values


_FIELD_LABELS = {
    "employee_id": "工号",
    "phone": "手机号",
    "name": "姓名",
    "department": "部门",
}
