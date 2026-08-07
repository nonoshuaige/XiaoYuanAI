"""Composable employee finders exposed through one factual lookup tool."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal, NotRequired, Protocol, TypedDict

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.integrations.mock_sandbox.client import (
    MockSandboxError,
    MockSandboxHttpClient,
)


class PersonInfo(TypedDict):
    employee_id: str
    name: str
    phone: str
    email: str
    department: str


class SearchError(TypedDict):
    code: Literal["service_error"]


PersonCondition = Literal[
    "employee_id",
    "phone",
    "phone_suffix",
    "name",
    "name_fragment",
]


class PeopleNoMatch(TypedDict):
    reason: Literal["condition_not_found", "conditions_conflict"]
    conditions: list[PersonCondition]


class PeopleSearchResult(TypedDict):
    people: list[PersonInfo]
    error: SearchError | None
    noMatch: NotRequired[PeopleNoMatch]


class FindPersonInput(BaseModel):
    """Independent lookup constraints that must all match returned people."""

    model_config = ConfigDict(extra="forbid")

    employee_id: str | None = Field(
        default=None,
        description="按工号精确筛选；1 至 5 位纯数字会左补零至 6 位。",
    )
    phone: str | None = Field(
        default=None,
        description="按完整手机号精确筛选。",
    )
    phone_suffix: str | None = Field(
        default=None,
        description="按手机尾号或手机号后几位筛选。",
    )
    name: str | None = Field(
        default=None,
        description="按完整姓名精确筛选。",
    )
    name_fragment: str | None = Field(
        default=None,
        description="按姓名片段包含关系筛选。",
    )

    @field_validator("phone", "phone_suffix")
    @classmethod
    def validate_phone_characters(cls, value: str | None) -> str | None:
        if value is not None and any(
            not character.isdigit() and character not in " +()-"
            for character in value.strip()
        ):
            raise ValueError("手机号只能包含数字和常见分隔符")
        return value

    @model_validator(mode="after")
    def normalize_and_require_condition(self) -> "FindPersonInput":
        labels = {
            "employee_id": "工号",
            "phone": "手机号",
            "phone_suffix": "手机号尾号",
            "name": "姓名",
            "name_fragment": "姓名片段",
        }
        for field_name, label in labels.items():
            value = getattr(self, field_name)
            if value is not None:
                normalized = value.strip()
                if not normalized:
                    raise ValueError(f"{label}不能为空")
                setattr(self, field_name, normalized)
        if not any(
            (
                self.employee_id,
                self.phone,
                self.phone_suffix,
                self.name,
                self.name_fragment,
            )
        ):
            raise ValueError("至少需要提供一个人员查询条件")
        return self


class PeopleDirectory(Protocol):
    def search(self, value: str) -> list[PersonInfo]: ...


class MockSandboxPeopleClient:
    """Expose the remote broad-match directory search as factual person data."""

    def __init__(self, http: MockSandboxHttpClient):
        self.http = http

    def search(self, value: str) -> list[PersonInfo]:
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
                str(payload.get("message") or "外部人员信息查询失败")
            )
        data = payload.get("data")
        users = data.get("user", []) if isinstance(data, dict) else []
        return [
            _sandbox_person(user)
            for user in users
            if isinstance(user, dict) and user.get("loginCode") and user.get("name")
        ]


def create_people_search_tools(directory: PeopleDirectory) -> tuple[BaseTool, ...]:
    """Expose one lookup Tool that intersects independent finder results."""

    @tool(
        "find_person",
        args_schema=FindPersonInput,
        description=(
            "按传入条件查询员工，所有非空参数是 AND 关系。people只包含满足全部条件的人员；"
            "people 为空表示没有共同匹配，此时noMatch区分某些条件无匹配和各条件指向不同人员；"
            "error表示服务异常。"
            "本工具只返回人员事实，不生成判断或回复。"
        ),
    )
    def find_person(
        employee_id: str | None = None,
        phone: str | None = None,
        phone_suffix: str | None = None,
        name: str | None = None,
        name_fragment: str | None = None,
    ) -> PeopleSearchResult:
        find_operations = (
            ("employee_id", employee_id, find_by_employee_id),
            ("phone", phone, find_by_phone),
            ("phone_suffix", phone_suffix, find_by_phone_suffix),
            ("name", name, find_by_name),
            ("name_fragment", name_fragment, find_by_name_fragment),
        )
        try:
            condition_groups = [
                (field_name, finder(directory, value))
                for field_name, value, finder in find_operations
                if value is not None
            ]
        except MockSandboxError:
            return {"people": [], "error": {"code": "service_error"}}
        people = _intersect_people(
            [group for _, group in condition_groups]
        )
        if people:
            return {"people": people, "error": None}
        unmatched_conditions = [
            field_name
            for field_name, group in condition_groups
            if not group
        ]
        return {
            "people": [],
            "error": None,
            "noMatch": {
                "reason": (
                    "condition_not_found"
                    if unmatched_conditions
                    else "conditions_conflict"
                ),
                "conditions": unmatched_conditions or [
                    field_name for field_name, _ in condition_groups
                ],
            },
        }

    return (find_person,)


def find_by_employee_id(
    directory: PeopleDirectory,
    value: str,
) -> list[PersonInfo]:
    """Return only people whose employee ID equals the normalized query."""
    normalized = normalize_employee_id(value)
    return _deduplicate_people(
        person
        for person in directory.search(normalized)
        if person["employee_id"].strip() == normalized
    )


def find_by_phone(directory: PeopleDirectory, value: str) -> list[PersonInfo]:
    """Return only people whose normalized phone equals the query."""
    normalized = normalize_phone(value)
    return _deduplicate_people(
        person
        for person in directory.search(normalized)
        if _phone_digits(person["phone"]) == normalized
    )


def find_by_phone_suffix(
    directory: PeopleDirectory,
    value: str,
) -> list[PersonInfo]:
    """Return only people whose normalized phone ends with the query."""
    normalized = normalize_phone(value)
    return _deduplicate_people(
        person
        for person in directory.search(normalized)
        if _phone_digits(person["phone"]).endswith(normalized)
    )


def find_by_name(directory: PeopleDirectory, value: str) -> list[PersonInfo]:
    """Return only people whose full name equals the query."""
    normalized = _require_value(value, label="姓名")
    return _deduplicate_people(
        person
        for person in directory.search(normalized)
        if person["name"].strip() == normalized
    )


def find_by_name_fragment(
    directory: PeopleDirectory,
    value: str,
) -> list[PersonInfo]:
    """Return only people whose name contains the supplied fragment."""
    normalized = _require_value(value, label="姓名片段")
    return _deduplicate_people(
        person
        for person in directory.search(normalized)
        if normalized in person["name"].strip()
    )


def normalize_employee_id(value: str) -> str:
    """Normalize a short numeric employee ID without changing other identifiers."""
    cleaned = _require_value(value, label="工号")
    if cleaned.isascii() and cleaned.isdigit() and len(cleaned) < 6:
        return cleaned.zfill(6)
    return cleaned


def normalize_phone(value: str) -> str:
    """Normalize common visual separators while preserving the supplied digits."""
    cleaned = _require_value(value, label="手机号")
    if any(
        not character.isdigit() and character not in " +()-"
        for character in cleaned
    ):
        raise ValueError("手机号只能包含数字和常见分隔符")
    normalized = _phone_digits(cleaned)
    if not normalized:
        raise ValueError("手机号必须包含数字")
    return normalized


def _intersect_people(groups: list[list[PersonInfo]]) -> list[PersonInfo]:
    if not groups:
        return []
    common_employee_ids = {person["employee_id"] for person in groups[0]}
    for group in groups[1:]:
        common_employee_ids.intersection_update(
            person["employee_id"] for person in group
        )
    return [
        person
        for person in groups[0]
        if person["employee_id"] in common_employee_ids
    ]


def _deduplicate_people(people: Iterable[PersonInfo]) -> list[PersonInfo]:
    deduplicated: dict[str, PersonInfo] = {}
    for person in people:
        deduplicated.setdefault(person["employee_id"], person)
    return list(deduplicated.values())


def _phone_digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _require_value(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label}不能为空")
    return normalized


def _sandbox_person(user: dict[str, Any]) -> PersonInfo:
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
        "email": str(
            user.get("email") or user.get("mail") or user.get("emailAddress") or ""
        ),
        "department": str(user.get("orgName") or user.get("orgFullName") or ""),
    }
