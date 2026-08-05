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
        identity_clues = {
            "employee_id": _clean(employee_id),
            "phone": _clean(phone),
            "name": _clean(name),
        }
        all_clues = {**identity_clues, "department": _clean(department)}
        primary_field = next(
            (
                field
                for field in ("employee_id", "phone", "name")
                if identity_clues[field]
            ),
            None,
        )
        if primary_field is None:
            return _empty_result(
                "invalid_input",
                "请至少提供工号、手机号或姓名中的一项。",
            )

        primary_matches = [
            person
            for person in self._search(identity_clues[primary_field] or "")
            if person[primary_field] == identity_clues[primary_field]
        ]
        checked_fields = [
            field
            for field in ("employee_id", "phone", "name", "department")
            if all_clues[field]
        ]
        if not primary_matches:
            return {
                "status": "not_found",
                "primary_field": primary_field,
                "matched_by": primary_field,
                "checked_fields": checked_fields,
                "conflicting_fields": [],
                "expanded_searches": {},
                "people": [],
                "message": (
                    f"没有找到匹配的员工（按{_FIELD_LABELS[primary_field]}查询）。"
                ),
                "source": "mock-sandbox",
            }

        remaining_clues = {
            field: value
            for field, value in all_clues.items()
            if field != primary_field and value
        }
        fully_matching_people = [
            person
            for person in primary_matches
            if all(person[field] == value for field, value in remaining_clues.items())
        ]
        if fully_matching_people:
            status = "found" if len(fully_matching_people) == 1 else "multiple_matches"
            message = (
                "已从外部通讯录查询并验证全部线索，找到 1 位员工。"
                if status == "found"
                else (
                    f"外部通讯录返回 {len(fully_matching_people)} 位匹配员工，"
                    "需要用户确认。"
                )
            )
            return {
                "status": status,
                "primary_field": primary_field,
                "matched_by": primary_field,
                "checked_fields": checked_fields,
                "conflicting_fields": [],
                "expanded_searches": {},
                "people": fully_matching_people,
                "message": message,
                "source": "mock-sandbox",
            }

        conflicting_fields = [
            field
            for field, value in remaining_clues.items()
            if any(person[field] != value for person in primary_matches)
        ]
        candidates = [
            _candidate(person, matched_by=primary_field) for person in primary_matches
        ]
        expanded_searches: dict[str, dict[str, str]] = {}
        expanded_candidate_count = 0
        for field in conflicting_fields:
            # Department is only a local validation clue and never starts a search.
            if field not in identity_clues:
                continue
            query_value = identity_clues[field] or ""
            matches = [
                person
                for person in self._search(query_value)
                if person[field] == query_value
            ]
            matched_candidates = [
                _candidate(person, matched_by=field) for person in matches
            ]
            expanded_candidate_count += len(matches)
            candidates.extend(matched_candidates)
            expanded_searches[field] = {
                "query_value": query_value,
                "status": "found" if matches else "not_found",
            }

        conflict_labels = "、".join(_FIELD_LABELS[field] for field in conflicting_fields)
        if expanded_candidate_count:
            message = (
                f"{_FIELD_LABELS[primary_field]}与{conflict_labels}匹配到不同员工，"
                "请注意区分并确认目标人员。"
            )
        else:
            message = (
                f"{_FIELD_LABELS[primary_field]}命中的员工与{conflict_labels}线索不一致。"
            )
        empty_search_messages = [
            f"已按{_FIELD_LABELS[field]}“{search['query_value']}”扩查，"
            "但未找到精确匹配员工。"
            for field, search in expanded_searches.items()
            if search["status"] == "not_found"
        ]
        if empty_search_messages:
            message = f"{message}{''.join(empty_search_messages)}请确认目标人员。"
        elif not expanded_candidate_count:
            message = f"{message}请核对并确认目标人员。"
        return {
            "status": "conflicting_candidates",
            "primary_field": primary_field,
            "matched_by": primary_field,
            "checked_fields": checked_fields,
            "conflicting_fields": conflicting_fields,
            "expanded_searches": expanded_searches,
            "people": candidates,
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
            "查询员工人事信息，可返回工号、姓名、手机号、部门等。用户明确要求找人、"
            "确认员工或查询员工信息时使用。调用前至少需要工号、手机号或姓名之一；"
            "如果都没有，先引导用户补充。调用时传入用户明确提供的全部线索。"
            "若返回 conflicting_candidates，必须展示全部 people 及匹配来源并请用户确认，"
            "不得自行选择。若 expanded_searches 中某字段 status=not_found，表示该线索"
            "已经查询但未命中，应告知用户核对，不要用相同线索重复查询。"
            "department 仅用于辅助校验，不能单独调用本工具。"
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
        "primary_field": None,
        "matched_by": None,
        "checked_fields": [],
        "conflicting_fields": [],
        "expanded_searches": {},
        "people": [],
        "message": message,
        "source": "mock-sandbox",
    }


def _candidate(person: dict[str, str], *, matched_by: str) -> dict[str, str]:
    return {**person, "matched_by": matched_by}


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
