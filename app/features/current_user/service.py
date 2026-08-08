"""Resolve and switch the process-local Mock Sandbox user."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.features.people.tools import (
    PeopleDirectory,
    filter_by_employee_id,
    normalize_employee_id,
)
from app.integrations.mock_sandbox.client import MockSandboxHttpClient


class CurrentUserNotFoundError(ValueError):
    """The requested employee ID does not identify one directory person."""


@dataclass(frozen=True)
class CurrentUser:
    employee_id: str
    name: str

    def as_dict(self) -> dict[str, str]:
        return {
            "employeeId": self.employee_id,
            "name": self.name,
        }


class CurrentUserService:
    """Use the people directory as the only source of employee names."""

    def __init__(
        self,
        http: MockSandboxHttpClient,
        directory: PeopleDirectory,
    ):
        self.http = http
        self.directory = directory

    def current(self) -> CurrentUser:
        settings = self.http.settings
        return CurrentUser(
            employee_id=settings.user_id,
            name=settings.user_name,
        )

    def resolve(self, employee_id: str) -> CurrentUser:
        normalized_id = normalize_employee_id(employee_id)
        people = self.directory.search(normalized_id)
        exact_matches = [
            person
            for person in filter_by_employee_id(people, normalized_id)
            if isinstance(person, dict)
            and str(person.get("name") or "").strip()
        ]
        if len(exact_matches) != 1:
            raise CurrentUserNotFoundError(
                f"工号 {normalized_id} 无法唯一对应一位员工，不能切换用户"
            )
        return _person_user(exact_matches[0])

    def switch(self, employee_id: str) -> CurrentUser:
        user = self.resolve(employee_id)
        self.http.set_user_identity(user.employee_id, user.name)
        return user


def _person_user(person: dict[str, Any]) -> CurrentUser:
    return CurrentUser(
        employee_id=str(person["employee_id"]).strip(),
        name=str(person["name"]).strip(),
    )
