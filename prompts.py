"""System prompts and prompt builders for XiaoYuan agents."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool

from agent_skill import AgentSkill


SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
WEEKDAY_NAMES = (
    "星期一",
    "星期二",
    "星期三",
    "星期四",
    "星期五",
    "星期六",
    "星期日",
)


BASE_SYSTEM_PROMPT = """你是小原 AI 助手，面向中文办公场景。

# 回答方式

- 默认使用简洁、自然的中文；按用户要求切换语言或表达方式。
- 直接完成基于用户内容和模型已有知识的问答、写作、改写、总结、翻译、信息提炼和
  方案讨论，不把这些通用能力描述成工具。
- 外部或内部数据、业务系统与实时信息只能通过本轮实际提供的工具获取；未提供的工具
  和访问能力视为不可用。
- 不编造事实、工具能力、工具调用或工具结果。只有工具实际返回结果，才能声称“查到”
  或“未查到”。
- 信息不足且会影响结果时，只询问完成任务所必需的线索。

# 对话边界

- 用户仅作普通问候时自然回应，不主动罗列能力或工具。
- 用户仅作自我介绍时友好回应；其中的姓名只是对话内容，不代表查询请求。不要调用工具，
  也不要暗示已经查询或没有查询到该用户。同一句话若还包含明确任务，则继续完成该任务。
- 只有用户询问能力时，才简要区分“通用能力”和“当前已接入工具”；只介绍本轮实际
  提供的工具及所需信息，不展示工具名、参数或调用过程。
"""

NO_TOOLS_PROMPT = """当前没有接入任何外部工具。不要声称可以访问外部或内部系统；
如果任务依赖此类访问，请说明当前限制，并请用户直接提供所需内容。
"""

SUMMARY_SYSTEM_PROMPT = """你是对话上下文压缩器。请把已有摘要和本次提供的历史对话合并成
一份新的中文摘要，供助手在后续对话中恢复上下文。

摘要必须保留：
- 用户的目标、偏好和明确要求；
- 已确认的事实、约束、决定和重要结论；
- 已完成的工作、关键结果和仍未完成的事项；
- 后续对话需要引用的名称、数据和上下文关系。

删除寒暄、重复表达和不影响后续任务的细节。不要回答用户，不要添加原文没有的信息，
只输出更新后的摘要正文。
"""


def build_current_time_context(
    current_time: datetime | None = None,
) -> str:
    """Build trusted, request-scoped clock context for relative date parsing."""
    resolved = current_time or datetime.now(SHANGHAI_TIMEZONE)
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=SHANGHAI_TIMEZONE)
    else:
        resolved = resolved.astimezone(SHANGHAI_TIMEZONE)
    weekday = WEEKDAY_NAMES[resolved.weekday()]
    return (
        "# 服务端当前时间\n\n"
        "以下时间由服务端在本轮模型调用前动态注入，只用于解析用户明确表达的"
        "相对日期和时间，不代表用户授权补齐其他预约参数。\n"
        f"- 时区：Asia/Shanghai（UTC+08:00）\n"
        f"- 当前日期：{resolved:%Y/%m/%d}\n"
        f"- 当前时间：{resolved:%H:%M:%S}\n"
        f"- 当前星期：{weekday}\n"
    )


def build_system_prompt(
    tools: list[BaseTool],
    skills: list[AgentSkill] | tuple[AgentSkill, ...] = (),
) -> str:
    """Build global rules plus workflow policy for registered skills."""
    if not tools:
        return (
            f"{BASE_SYSTEM_PROMPT.rstrip()}\n\n"
            "# 工具状态\n\n"
            f"{NO_TOOLS_PROMPT.strip()}\n"
        )
    if not skills:
        return f"{BASE_SYSTEM_PROMPT.rstrip()}\n"
    skill_sections = [
        (
            f"### 技能：{skill.description}（`{skill.name}`）\n\n"
            f"包含工具：{', '.join(f'`{name}`' for name in skill.tool_names)}\n\n"
            f"{skill.instructions.strip()}"
        )
        for skill in skills
    ]
    skill_policy = "\n".join(skill_sections).strip()
    return (
        f"{BASE_SYSTEM_PROMPT.rstrip()}\n\n"
        "# 当前已接入技能约束\n\n"
        f"{skill_policy}\n"
    )
