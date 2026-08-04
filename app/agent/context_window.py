"""Context-window budgeting and per-request token usage helpers."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool


CONTEXT_WINDOW_OPTIONS = (8_192, 16_384, 32_768, 65_536)
DEFAULT_CONTEXT_WINDOW_TOKENS = 16_384
_CJK_PATTERN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
)


@dataclass(frozen=True)
class ContextPlan:
    messages: tuple[BaseMessage, ...]
    window_tokens: int
    estimated_input_tokens: int
    truncated: bool
    dropped_rounds: int
    summary_dropped: bool


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


def validate_context_window_tokens(value: int | None) -> int:
    resolved = value or DEFAULT_CONTEXT_WINDOW_TOKENS
    if resolved not in CONTEXT_WINDOW_OPTIONS:
        choices = "、".join(str(option) for option in CONTEXT_WINDOW_OPTIONS)
        raise ValueError(f"上下文限制只支持：{choices} tokens")
    return resolved


def plan_context(
    *,
    window_tokens: int,
    system_prompt: str,
    tools: Sequence[BaseTool],
    fixed_messages: Sequence[BaseMessage],
    summary_message: BaseMessage | None,
    history_groups: Sequence[Sequence[BaseMessage]],
    current_message: BaseMessage,
) -> ContextPlan:
    """Keep newest complete rounds while enforcing an estimated input budget."""
    resolved_window = validate_context_window_tokens(window_tokens)
    selected_groups = [list(group) for group in history_groups]
    selected_summary = summary_message
    static_tokens = estimate_text_tokens(system_prompt) + estimate_tools_tokens(tools)

    def candidate_messages() -> list[BaseMessage]:
        return [
            *fixed_messages,
            *([selected_summary] if selected_summary is not None else []),
            *(message for group in selected_groups for message in group),
            current_message,
        ]

    messages = candidate_messages()
    estimated = static_tokens + estimate_messages_tokens(messages)
    dropped_rounds = 0
    while estimated > resolved_window and selected_groups:
        selected_groups.pop(0)
        dropped_rounds += 1
        messages = candidate_messages()
        estimated = static_tokens + estimate_messages_tokens(messages)

    summary_dropped = False
    if estimated > resolved_window and selected_summary is not None:
        selected_summary = None
        summary_dropped = True
        messages = candidate_messages()
        estimated = static_tokens + estimate_messages_tokens(messages)

    if estimated > resolved_window:
        raise ValueError(
            "当前消息、系统规则和工具定义已超过所选上下文限制，"
            "请提高上下文限制或缩短当前消息"
        )

    return ContextPlan(
        messages=tuple(messages),
        window_tokens=resolved_window,
        estimated_input_tokens=estimated,
        truncated=bool(dropped_rounds or summary_dropped),
        dropped_rounds=dropped_rounds,
        summary_dropped=summary_dropped,
    )


def estimate_text_tokens(value: str) -> int:
    """Estimate mixed Chinese/Latin text without requiring a model tokenizer."""
    if not value:
        return 0
    cjk_count = len(_CJK_PATTERN.findall(value))
    non_cjk_count = len(value) - cjk_count
    return cjk_count + math.ceil(non_cjk_count / 4)


def estimate_messages_tokens(messages: Iterable[BaseMessage]) -> int:
    total = 0
    for message in messages:
        serialized = _serialized_message_payload(message)
        total += 4 + estimate_text_tokens(serialized)
    return total


def estimate_tools_tokens(tools: Sequence[BaseTool]) -> int:
    total = 0
    for registered_tool in tools:
        schema: Any = None
        if registered_tool.args_schema is not None:
            schema = registered_tool.args_schema.model_json_schema()
        serialized = json.dumps(
            {
                "name": registered_tool.name,
                "description": registered_tool.description,
                "parameters": schema,
            },
            ensure_ascii=False,
            default=str,
        )
        total += 12 + estimate_text_tokens(serialized)
    return total


def aggregate_message_usage(messages: Iterable[BaseMessage]) -> TokenUsage | None:
    """Sum every model step in one Agent run, including tool-call turns."""
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    found = False
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        usage = message.usage_metadata or {}
        if usage:
            current_input = _usage_int(usage, "input_tokens")
            current_output = _usage_int(usage, "output_tokens")
            current_total = _usage_int(usage, "total_tokens")
        else:
            provider_usage = (message.response_metadata or {}).get("token_usage", {})
            current_input = _usage_int(provider_usage, "prompt_tokens")
            current_output = _usage_int(provider_usage, "completion_tokens")
            current_total = _usage_int(provider_usage, "total_tokens")
        if current_input or current_output or current_total:
            found = True
        input_tokens += current_input
        output_tokens += current_output
        total_tokens += current_total or current_input + current_output
    if not found:
        return None
    return TokenUsage(input_tokens, output_tokens, total_tokens)


def resolve_request_usage(
    messages: Sequence[BaseMessage],
    *,
    initial_input_tokens: int,
    initial_message_count: int,
) -> tuple[TokenUsage | None, bool]:
    """Use Provider usage when present and estimate only missing model steps."""
    running_input_tokens = initial_input_tokens
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    found = False
    estimated = False
    for index, message in enumerate(messages):
        if index < initial_message_count:
            continue
        if isinstance(message, AIMessage):
            found = True
            real_usage = aggregate_message_usage([message])
            if real_usage is None:
                estimated = True
                current_input = running_input_tokens
                current_output = estimate_text_tokens(
                    _serialized_message_payload(message)
                )
                current_total = current_input + current_output
            else:
                current_input = real_usage.input_tokens
                current_output = real_usage.output_tokens
                current_total = real_usage.total_tokens
            input_tokens += current_input
            output_tokens += current_output
            total_tokens += current_total
        running_input_tokens += estimate_messages_tokens([message])
    if not found:
        return None, estimated
    return TokenUsage(input_tokens, output_tokens, total_tokens), estimated


def combine_token_usage(*usages: TokenUsage | None) -> TokenUsage | None:
    resolved = [usage for usage in usages if usage is not None]
    if not resolved:
        return None
    return TokenUsage(
        input_tokens=sum(usage.input_tokens for usage in resolved),
        output_tokens=sum(usage.output_tokens for usage in resolved),
        total_tokens=sum(usage.total_tokens for usage in resolved),
    )


def _usage_int(source: Any, key: str) -> int:
    if not isinstance(source, dict):
        return 0
    value = source.get(key)
    return int(value) if isinstance(value, (int, float)) and value >= 0 else 0


def _serialized_message_payload(message: BaseMessage) -> str:
    payload: dict[str, Any] = {"content": message.content}
    if isinstance(message, AIMessage):
        if message.tool_calls:
            payload["tool_calls"] = message.tool_calls
        if message.invalid_tool_calls:
            payload["invalid_tool_calls"] = message.invalid_tool_calls
    return json.dumps(payload, ensure_ascii=False, default=str)
