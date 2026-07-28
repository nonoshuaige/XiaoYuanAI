"""Capture Provider HTTP responses and serialize LangChain model messages."""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx
from langchain_core.messages import AIMessage


@dataclass
class ModelCallCapture:
    """All Provider responses observed during one logical Agent model call."""

    model_id: str
    provider_responses: list[dict[str, Any]] = field(default_factory=list)


_active_capture: ContextVar[ModelCallCapture | None] = ContextVar(
    "xiaoyuan_active_model_capture",
    default=None,
)


@contextmanager
def capture_model_call(model_id: str) -> Iterator[ModelCallCapture]:
    """Associate HTTP response hooks with the current session model call."""
    capture = ModelCallCapture(model_id=model_id)
    token = _active_capture.set(capture)
    try:
        yield capture
    finally:
        _active_capture.reset(token)


def create_audited_http_client(
    provider_id: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """Build the shared sync client used by one cached ChatOpenAI instance."""

    def capture_response(response: httpx.Response) -> None:
        capture = _active_capture.get()
        if capture is None:
            return

        body_error: str | None = None
        try:
            response.read()
            raw_body = response.text
        except Exception as exc:  # pragma: no cover - defensive transport path
            raw_body = ""
            body_error = f"{type(exc).__name__}: {exc}"

        parsed_body: Any = None
        if raw_body:
            try:
                parsed_body = json.loads(raw_body)
            except (TypeError, ValueError):
                pass

        headers = list(response.headers.multi_items())
        request_id = _find_request_id(response.headers, parsed_body)
        response_id = (
            parsed_body.get("id")
            if isinstance(parsed_body, dict)
            and isinstance(parsed_body.get("id"), str)
            else None
        )
        captured_response: dict[str, Any] = {
            "provider_id": provider_id,
            "request": {
                "method": response.request.method,
                "url": str(response.request.url),
            },
            "status_code": response.status_code,
            "reason_phrase": response.reason_phrase,
            # A list preserves duplicate response headers without collapsing them.
            "headers": [[name, value] for name, value in headers],
            "request_id": request_id,
            "response_id": response_id,
            # Keep the exact decoded payload and a convenient parsed representation.
            "raw_body": raw_body,
            "json_body": parsed_body,
        }
        if body_error:
            captured_response["body_read_error"] = body_error
        capture.provider_responses.append(captured_response)

    return httpx.Client(
        event_hooks={"response": [capture_response]},
        transport=transport,
    )


def serialize_ai_message(message: AIMessage) -> dict[str, Any]:
    """Return every public AIMessage field as JSON-compatible data."""
    return message.model_dump(
        mode="json",
        warnings=False,
        fallback=_json_fallback,
        serialize_as_any=True,
    )


def _find_request_id(
    headers: httpx.Headers,
    parsed_body: Any,
) -> str | None:
    for name in (
        "x-request-id",
        "request-id",
        "x-ms-request-id",
        "x-amzn-requestid",
    ):
        value = headers.get(name)
        if value:
            return value
    if isinstance(parsed_body, dict):
        for name in ("request_id", "requestId"):
            value = parsed_body.get(name)
            if isinstance(value, str) and value:
                return value
    return None


def _json_fallback(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", warnings=False, fallback=str)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
