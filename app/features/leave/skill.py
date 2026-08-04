"""Workflow policy for leave balance, application, and cancellation tools."""

from __future__ import annotations

from app.agent.skill import AgentSkill
from app.features.leave.tools import MockSandboxLeaveClient, create_leave_tools


LEAVE_SKILL_INSTRUCTIONS = """## 执行流程

- 用户询问假期余额、想请假或要提交请假时，先调用queryLeaveBalance获取真实类型和余额。
- 当前只支持年休假和事假；用户没有说明类型、日期、全天/上午/下午或事由时，只追问缺失项。
- 相对日期必须依据本轮服务端时间换算成yyyy/MM/dd，不得猜测日期。
- 调用applyLeave前，必须向用户复述请假类型、日期、时段、事由，并取得本轮明确的提交确认。
- 用户尚未明确确认时不得把confirmed设为true，不得调用applyLeave，也不得声称申请已提交。
- applyLeave返回真实requestId后才能声称提交成功，并把requestId告知用户以便后续撤销。
- 撤销必须提供requestId并取得用户明确确认，然后调用cancelLeave；不得猜测申请ID。
- 余额不足、日期冲突、重复撤销或沙箱不可用时，如实返回错误，不擅自更换假期类型或日期。
"""


def create_leave_skill(client: MockSandboxLeaveClient) -> AgentSkill:
    return AgentSkill(
        name="leave-request",
        description="查询假期余额、提交年休假或事假，以及按申请ID撤销请假。",
        instructions=LEAVE_SKILL_INSTRUCTIONS,
        tools=tuple(create_leave_tools(client)),
    )
