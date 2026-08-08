"""One employee lookup Tool backed by a local deterministic filter pipeline."""

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
    "name",
    "phone_suffix",
    "department",
]


class PeopleNoMatch(TypedDict):
    reason: Literal["no_common_match"]
    conditions: list[PersonCondition]


class PeopleSearchResult(TypedDict):
    people: list[PersonInfo]
    error: SearchError | None
    noMatch: NotRequired[PeopleNoMatch]


class FindPersonInput(BaseModel):
    """Primary lookup constraints plus an optional department filter."""

    model_config = ConfigDict(extra="forbid")

    employee_id: str | None = Field(
        default=None,
        description="按工号精确筛选；1 至 5 位纯数字会左补零至 6 位。",
    )
    phone: str | None = Field(
        default=None,
        description="按完整手机号精确筛选。",
    )
    name: str | None = Field(
        default=None,
        description="按姓名字段包含关系筛选，例如“涵”可匹配姓名中包含“涵”的员工。",
    )
    phone_suffix: str | None = Field(
        default=None,
        description="按手机尾号或手机号后几位筛选。",
    )
    department: str | None = Field(
        default=None,
        description=(
            "按部门名称精确筛选已有候选；只能与工号、完整手机号、姓名或手机尾号共同使用。"
        ),
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
            "name": "姓名",
            "phone_suffix": "手机号尾号",
            "department": "部门",
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
                self.name,
                self.phone_suffix,
            )
        ):
            raise ValueError("工号、完整手机号、姓名或手机尾号至少需要提供一个")
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
    """Expose one factual employee lookup Tool."""

    @tool(
        "find_person",
        args_schema=FindPersonInput,
        description=(
            "根据人员定位条件查询员工事实。工号、完整手机号、姓名或手机尾号至少提供一个；"
            "部门只能作为附加过滤条件，不能单独查询。姓名按字段包含关系匹配，所有非空参数"
            "都是AND关系。工具按工号、完整手机号、姓名、手机尾号的固定优先级，使用首个非空"
            "条件调用一次人员目录，再对候选应用全部非空条件的本地字段过滤。people只包含同时"
            "满足全部条件的人员；people为空表示没有共同匹配；error表示服务异常。工具只负责"
            "找人并返回人员事实，不判断用户陈述、不进行身份认证，也不生成面向用户的回复。"
        ),
    )
    def find_person(
        employee_id: str | None = None,
        phone: str | None = None,
        name: str | None = None,
        phone_suffix: str | None = None,
        department: str | None = None,
    ) -> PeopleSearchResult:
        try:
            lookup_value = select_lookup_value(
                employee_id=employee_id,
                phone=phone,
                name=name,
                phone_suffix=phone_suffix,
            )
            candidates = directory.search(lookup_value)
        except MockSandboxError:
            return {"people": [], "error": {"code": "service_error"}}
        people = apply_people_filters(
            candidates,
            employee_id=employee_id,
            phone=phone,
            name=name,
            phone_suffix=phone_suffix,
            department=department,
        )
        if people:
            return {"people": people, "error": None}
        return {
            "people": [],
            "error": None,
            "noMatch": {
                "reason": "no_common_match",
                "conditions": active_conditions(
                    employee_id=employee_id,
                    phone=phone,
                    name=name,
                    phone_suffix=phone_suffix,
                    department=department,
                ),
            },
        }

    return (find_person,)


def select_lookup_value(
    *,
    employee_id: str | None,
    phone: str | None,
    name: str | None,
    phone_suffix: str | None,
) -> str:
    """Choose and normalize one remote lookup value by fixed selectivity."""
    if employee_id is not None:
        return normalize_employee_id(employee_id)
    if phone is not None:
        return normalize_phone(phone)
    if name is not None:
        return normalize_name(name)
    if phone_suffix is not None:
        return normalize_phone(phone_suffix)
    raise ValueError("缺少主要人员查询条件")


def apply_people_filters(
    people: list[PersonInfo],
    *,
    employee_id: str | None,
    phone: str | None,
    name: str | None,
    phone_suffix: str | None,
    department: str | None,
) -> list[PersonInfo]:
    """Apply every supplied condition as an AND filter to one candidate list."""
    filtered = _deduplicate_people(people)
    operations = (
        (employee_id, filter_by_employee_id),
        (phone, filter_by_phone),
        (name, filter_by_name),
        (phone_suffix, filter_by_phone_suffix),
        (department, filter_by_department),
    )
    for value, filter_people in operations:
        if value is not None:
            filtered = filter_people(filtered, value)
        if not filtered:
            break
    return filtered


def filter_by_employee_id(
    people: list[PersonInfo],
    value: str,
) -> list[PersonInfo]:
    """Keep only people whose employee ID exactly equals the normalized value."""
    normalized = normalize_employee_id(value)
    return list(
        person
        for person in people
        if person["employee_id"].strip() == normalized
    )


def filter_by_phone(people: list[PersonInfo], value: str) -> list[PersonInfo]:
    """Keep only people whose normalized phone exactly equals the value."""
    normalized = normalize_phone(value)
    return list(
        person
        for person in people
        if _phone_digits(person["phone"]) == normalized
    )


def filter_by_phone_suffix(
    people: list[PersonInfo],
    value: str,
) -> list[PersonInfo]:
    """Keep only people whose normalized phone ends with the value."""
    normalized = normalize_phone(value)
    return list(
        person
        for person in people
        if _phone_digits(person["phone"]).endswith(normalized)
    )


def filter_by_name(people: list[PersonInfo], value: str) -> list[PersonInfo]:
    """Keep people whose name field contains the supplied text."""
    normalized = normalize_name(value)
    return list(
        person
        for person in people
        if normalized in person["name"].strip()
    )


def filter_by_department(
    people: list[PersonInfo],
    value: str,
) -> list[PersonInfo]:
    """Keep only people whose department exactly equals the supplied value."""
    normalized = _require_value(value, label="部门")
    return list(
        person
        for person in people
        if person["department"].strip() == normalized
    )


def active_conditions(
    *,
    employee_id: str | None,
    phone: str | None,
    name: str | None,
    phone_suffix: str | None,
    department: str | None,
) -> list[PersonCondition]:
    """List supplied conditions in the same stable order as the filter pipeline."""
    values = (
        ("employee_id", employee_id),
        ("phone", phone),
        ("name", name),
        ("phone_suffix", phone_suffix),
        ("department", department),
    )
    return [field_name for field_name, value in values if value is not None]


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


def normalize_name(value: str) -> str:
    """Normalize text used for both remote name lookup and local contains matching."""
    return _require_value(value, label="姓名")


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
