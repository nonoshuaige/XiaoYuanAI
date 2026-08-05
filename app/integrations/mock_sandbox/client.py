"""Shared HTTP client for the local Mock Sandbox compatibility API."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx
from dotenv import load_dotenv


load_dotenv()


class MockSandboxError(RuntimeError):
    """The sandbox was unavailable or returned an invalid response."""


class MockSandboxConflictError(MockSandboxError):
    """The requested sandbox write conflicts with existing state."""


@dataclass(frozen=True)
class MockSandboxSettings:
    base_url: str
    timeout_seconds: float
    user_id: str
    user_name: str

    @classmethod
    def from_env(cls) -> "MockSandboxSettings":
        base_url = os.getenv(
            "XIAOYUAN_MOCK_SANDBOX_URL",
            "http://127.0.0.1:18080",
        ).strip().rstrip("/")
        if not base_url:
            raise RuntimeError("XIAOYUAN_MOCK_SANDBOX_URL 不能为空")
        try:
            timeout = float(
                os.getenv("XIAOYUAN_MOCK_SANDBOX_TIMEOUT", "5").strip()
            )
        except ValueError as exc:
            raise RuntimeError(
                "XIAOYUAN_MOCK_SANDBOX_TIMEOUT 必须是数字"
            ) from exc
        if not 0 < timeout <= 60:
            raise RuntimeError(
                "XIAOYUAN_MOCK_SANDBOX_TIMEOUT 必须在 0 到 60 秒之间"
            )
        return cls(
            base_url=base_url,
            timeout_seconds=timeout,
            user_id=os.getenv("XIAOYUAN_MOCK_USER_ID", "000328").strip(),
            user_name=os.getenv("XIAOYUAN_MOCK_USER_NAME", "郑子涵").strip(),
        )


class MockSandboxHttpClient:
    def __init__(self, settings: MockSandboxSettings | None = None):
        self._settings = settings or MockSandboxSettings.from_env()
        self._settings_lock = threading.RLock()
        self._client = httpx.Client(
            base_url=self._settings.base_url,
            timeout=self._settings.timeout_seconds,
            headers={"Accept": "application/json"},
        )

    @property
    def settings(self) -> MockSandboxSettings:
        with self._settings_lock:
            return self._settings

    def set_user_identity(self, user_id: str, user_name: str) -> None:
        """Atomically replace the process-local Mock Sandbox identity."""
        normalized_id = user_id.strip()
        normalized_name = user_name.strip()
        if not normalized_id or not normalized_name:
            raise ValueError("工号和姓名不能为空")
        with self._settings_lock:
            current = self._settings
            self._settings = MockSandboxSettings(
                base_url=current.base_url,
                timeout_seconds=current.timeout_seconds,
                user_id=normalized_id,
                user_name=normalized_name,
            )

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method,
                path,
                params=params,
                json=json_body,
                data=form,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise MockSandboxError(
                f"无法连接测试沙箱：{type(exc).__name__}"
            ) from exc
        if response.status_code == 409:
            raise MockSandboxConflictError(_response_error(response))
        if response.is_error:
            raise MockSandboxError(
                f"测试沙箱请求失败（HTTP {response.status_code}）："
                f"{_response_error(response)}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MockSandboxError("测试沙箱返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise MockSandboxError("测试沙箱返回格式错误：顶层必须是对象")
        return payload


def _response_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:500] or "未知错误"
    if isinstance(payload, dict):
        return str(
            payload.get("detail")
            or payload.get("message")
            or payload.get("title")
            or "未知错误"
        )
    return "未知错误"


@lru_cache(maxsize=1)
def get_mock_sandbox_client() -> MockSandboxHttpClient:
    return MockSandboxHttpClient()
