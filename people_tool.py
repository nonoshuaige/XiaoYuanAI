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
        description="用户明确提供的工号，例如 E1024；不要猜测或编造。",
    )
    phone: str | None = Field(
        default=None,
        description="用户明确提供的手机号；不要猜测或编造。",
    )
    name: str | None = Field(
        default=None,
        description="用户明确提供的姓名；不要猜测或编造。",
    )
    department: str | None = Field(
        default=None,
        description="用户明确提供的部门，可用于缩小结果范围；不能单独用于找人。",
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
        """Find employees using employee ID > phone > name precedence."""
        clues = {
            "employee_id": _clean(employee_id),
            "phone": _clean(phone),
            "name": _clean(name),
        }
        department = _clean(department)
        selected_field = next(
            (field for field in ("employee_id", "phone", "name") if clues[field]),
            None,
        )
        if selected_field is None:
            return {
                "status": "invalid_input",
                "matched_by": None,
                "ignored_fields": [],
                "people": [],
                "message": "请至少提供工号、手机号或姓名中的一项。",
            }

        ignored_fields = [
            field
            for field in ("employee_id", "phone", "name")
            if field != selected_field and clues[field]
        ]
        sql = (
            "SELECT employee_id, name, phone, department "
            f"FROM people WHERE {selected_field} = ?"
        )
        parameters: list[str] = [clues[selected_field] or ""]
        if department:
            sql += " AND department = ?"
            parameters.append(department)
        sql += " ORDER BY employee_id LIMIT 21"

        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        people = [dict(row) for row in rows]
        if not people:
            status = "not_found"
            message = f"没有找到匹配的员工（按{_FIELD_LABELS[selected_field]}查询）。"
        elif len(people) == 1:
            status = "found"
            message = f"已按{_FIELD_LABELS[selected_field]}找到 1 位员工。"
        else:
            status = "multiple_matches"
            message = (
                f"按{_FIELD_LABELS[selected_field]}找到 {len(people)} 位员工，"
                "需要结合部门或其他信息请用户确认。"
            )
        return {
            "status": status,
            "matched_by": selected_field,
            "ignored_fields": ignored_fields,
            "people": people,
            "message": message,
        }


def create_find_person_tool(store: PeopleStore) -> BaseTool:
    """Bind one employee repository to the Agent's natural-language tool."""

    @tool(
        "find_person",
        args_schema=FindPersonInput,
        description=(
            "找人：仅当用户明确要求找人、查人、确认人员或查询员工联系方式，"
            "并且已经提供工号、手机号、姓名中的至少一项时，才从员工通讯录查询。"
            "用户有找人意图但没有上述线索时不要调用，应先询问；自我介绍、寒暄、"
            "署名、写作或翻译内容、一般性提及姓名时不要调用。"
            "同时提供多项时严格按工号 > 手机号 > 姓名的优先级查询。"
            "部门只能作为可选的辅助过滤条件，不能单独用于找人。"
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
}
