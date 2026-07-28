"""Small runtime abstraction for grouping tools with one business workflow."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.tools import BaseTool


@dataclass(frozen=True)
class AgentSkill:
    """A named workflow whose instructions and tools are registered together."""

    name: str
    description: str
    instructions: str
    tools: tuple[BaseTool, ...]

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(registered_tool.name for registered_tool in self.tools)
