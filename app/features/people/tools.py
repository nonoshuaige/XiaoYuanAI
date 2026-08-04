"""Employee search tool backed exclusively by the external Mock Sandbox."""

from __future__ import annotations

from typing import Any, Protocol

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, model_validator

from app.integrations.mock_sandbox.client import (
    MockSandboxError,
    MockSandboxHttpClient,
)


class FindPersonInput(BaseModel):
    """Validated clues extracted from the user's natural-language request."""

    employee_id: str | None = Field(
        default=None,
        description="用户提供的工号；未提供时留空。",
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


class PeopleDirectory(Protocol):
    def find(
        self,
        *,
        employee_id: str | None = None,
        phone: str | None = None,
        name: str | None = None,
        department: str | None = None,
    ) -> dict[str, Any]: ...


class MockSandboxPeopleClient:
    """Search the external address book without any local directory cache."""

    def __init__(self, http: MockSandboxHttpClient):
        self.http = http

    def find(
        self,
        *,
        employee_id: str | None = None,
        phone: str | None = None,
        name: str | None = None,
        department: str | None = None,
    ) -> dict[str, Any]:
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
            return _empty_result(
                "invalid_input",
                "请至少提供工号、手机号或姓名中的一项。",
            )

        primary_matches = [
            person
            for person in self._search(clues[selected_field] or "")
            if person[selected_field] == clues[selected_field]
        ]
        alternative_matched_fields = []
        if not primary_matches:
            for field in ("employee_id", "phone", "name"):
                if field == selected_field or not clues[field]:
                    continue
                if any(
                    person[field] == clues[field]
                    for person in self._search(clues[field] or "")
                ):
                    alternative_matched_fields.append(field)

        additional_clues = {
            field: value
            for field, value in all_clues.items()
            if field != selected_field and value
        }
        people = [
            person
            for person in primary_matches
            if all(person[field] == value for field, value in additional_clues.items())
        ]
        checked_fields = [
            field
            for field in ("employee_id", "phone", "name", "department")
            if all_clues[field]
        ]
        conflicting_fields: list[str] = []
        if not primary_matches:
            if alternative_matched_fields:
                status = "conflicting_clues"
                conflicting_fields = [
                    field
                    for field in ("employee_id", "phone", "name")
                    if clues[field]
                ]
                message = "提供的身份线索无法指向同一位员工，请核对后重试。"
            else:
                status = "not_found"
                message = (
                    f"没有找到匹配的员工（按{_FIELD_LABELS[selected_field]}查询）。"
                )
        elif not people:
            status = "conflicting_clues"
            conflicting_fields = [
                field
                for field, value in additional_clues.items()
                if all(person[field] != value for person in primary_matches)
            ]
            message = "提供的线索与外部通讯录记录不一致，请核对后重试。"
        elif len(people) == 1:
            status = "found"
            message = "已从外部通讯录查询并验证全部线索，找到 1 位员工。"
        else:
            status = "multiple_matches"
            message = f"外部通讯录返回 {len(people)} 位匹配员工，需要用户确认。"
        return {
            "status": status,
            "matched_by": selected_field,
            "checked_fields": checked_fields,
            "conflicting_fields": conflicting_fields,
            "people": people,
            "message": message,
            "source": "mock-sandbox",
        }

    def _search(self, value: str) -> list[dict[str, str]]:
        payload = self.http.request_json(
            "GET",
            "/api/eop-olk/api/v2/addressbook/search",
            params={
                "searchType": 2,
                "searchValue": value,
                "page": 1,
                "size": 100,
            },
            headers={
                "X-LOGINCODE": self.http.settings.user_id,
                "SYSID": "2",
            },
        )
        if int(payload.get("code", -1)) != 0:
            raise MockSandboxError(
                str(payload.get("message") or "外部通讯录查询失败")
            )
        data = payload.get("data")
        users = data.get("user", []) if isinstance(data, dict) else []
        return [
            _sandbox_person(user)
            for user in users
            if isinstance(user, dict) and user.get("loginCode") and user.get("name")
        ]


def create_find_person_tool(directory: PeopleDirectory) -> BaseTool:
    """Bind the external employee directory to the Agent tool."""

    @tool(
        "find_person",
        args_schema=FindPersonInput,
        description=(
            "查询外部员工通讯录。仅当用户明确要求找人、确认员工或查询联系方式，"
            "并已提供工号、手机号或姓名之一时调用；满足条件就立即调用。"
            "把用户明确提供的全部线索如实传入；工具会校验线索是否一致，"
            "冲突时不得选择某个人。部门只能辅助查询，不能单独用于找人。"
        ),
    )
    def find_person(
        employee_id: str | None = None,
        phone: str | None = None,
        name: str | None = None,
        department: str | None = None,
    ) -> dict[str, Any]:
        try:
            return directory.find(
                employee_id=employee_id,
                phone=phone,
                name=name,
                department=department,
            )
        except MockSandboxError as exc:
            return _empty_result("service_error", str(exc))

    return find_person


def _clean(value: str | None) -> str | None:
    normalized = value.strip() if value is not None else ""
    return normalized or None


def _empty_result(status: str, message: str) -> dict[str, Any]:
    return {
        "status": status,
        "matched_by": None,
        "checked_fields": [],
        "conflicting_fields": [],
        "people": [],
        "message": message,
        "source": "mock-sandbox",
    }


_FIELD_LABELS = {
    "employee_id": "工号",
    "phone": "手机号",
    "name": "姓名",
    "department": "部门",
}


def _sandbox_person(user: dict[str, Any]) -> dict[str, str]:
    phones = (
        user.get("telPhone"),
        user.get("telPhone1"),
        user.get("telPhone2"),
        user.get("workPhone"),
    )
    return {
        "employee_id": str(user.get("loginCode") or ""),
        "name": str(user.get("name") or ""),
        "phone": next((str(value) for value in phones if value), ""),
        "department": str(user.get("orgName") or user.get("orgFullName") or ""),
    }
