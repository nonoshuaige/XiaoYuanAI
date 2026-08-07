"""Leave-request tools backed by the Mock Sandbox compatibility API."""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.integrations.mock_sandbox.client import (
    MockSandboxError,
    MockSandboxHttpClient,
)


LEAVE_TYPE_IDS = {
    "年休假": "mock-annual-leave-id",
    "事假": "mock-personal-leave-id",
}
LEAVE_PERIOD_TYPES = {"全天": "0", "上午": "1", "下午": "2"}


class LeaveToolInput(BaseModel):
    """Reject undeclared fields so only the explicit Tool contract is accepted."""

    model_config = ConfigDict(extra="forbid")


class QueryLeaveInput(LeaveToolInput):
    employeeId: str | None = Field(
        default=None,
        description="员工工号；未提供时使用当前沙箱用户",
    )


class ApplyLeaveInput(LeaveToolInput):
    employeeId: str | None = Field(
        default=None,
        description="员工工号；未提供时使用当前沙箱用户",
    )
    leaveType: Literal["年休假", "事假"]
    startDate: str = Field(description="开始日期，格式yyyy/MM/dd")
    endDate: str = Field(description="结束日期，格式yyyy/MM/dd")
    period: Literal["全天", "上午", "下午"] = "全天"
    reason: str = Field(min_length=1, max_length=200)
    confirmed: Literal[True] = Field(
        description="只有用户明确确认提交当前请假参数时才能为true"
    )

    @model_validator(mode="after")
    def validate_dates(self) -> "ApplyLeaveInput":
        from datetime import datetime

        try:
            start = datetime.strptime(self.startDate, "%Y/%m/%d").date()
            end = datetime.strptime(self.endDate, "%Y/%m/%d").date()
        except ValueError as exc:
            raise ValueError("请假日期必须使用yyyy/MM/dd格式") from exc
        if end < start:
            raise ValueError("请假结束日期不能早于开始日期")
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("请假事由不能为空")
        return self


class CancelLeaveInput(LeaveToolInput):
    requestId: str = Field(min_length=1, max_length=64)
    employeeId: str | None = Field(
        default=None,
        description="原申请员工工号；未提供时使用当前沙箱用户",
    )
    confirmed: Literal[True] = Field(
        description="只有用户明确确认撤销该申请时才能为true"
    )


class MockSandboxLeaveClient:
    def __init__(self, http: MockSandboxHttpClient):
        self.http = http

    def query(self, employee_id: str | None = None) -> dict[str, Any]:
        resolved_id, token, virtual_user_id = self._login(employee_id)
        leave_types = self._rest(token, "getVacationsItem", {})
        balances = []
        for leave_type in leave_types:
            leave_type_id = str(leave_type.get("id") or "")
            template = self._rest(
                token,
                "getTimeTemplate",
                {"param": leave_type_id},
            )
            balances.append(
                {
                    "leaveType": leave_type.get("name"),
                    "leaveTypeId": leave_type_id,
                    "code": leave_type.get("code"),
                    "remainingDays": _remaining_days(template),
                }
            )
        return {
            "success": True,
            "employeeId": resolved_id,
            "virtualUserId": virtual_user_id,
            "balances": balances,
        }

    def apply(
        self,
        *,
        employee_id: str | None,
        leave_type: str,
        start_date: str,
        end_date: str,
        period: str,
        reason: str,
    ) -> dict[str, Any]:
        resolved_id, token, virtual_user_id = self._login(employee_id)
        leave_type_id = LEAVE_TYPE_IDS[leave_type]
        result = self._rest(
            token,
            "saveApplicationDTO",
            {
                "dataObj": {
                    "TB_TMG_APPLICATION_REPORT": {
                        "CUSTOM_DURATION": {
                            "type": "null",
                            "value": json.dumps(
                                [
                                    {
                                        "begin": start_date.replace("/", "-"),
                                        "end": end_date.replace("/", "-"),
                                        "type": LEAVE_PERIOD_TYPES[period],
                                    }
                                ],
                                ensure_ascii=False,
                            ),
                        }
                    },
                    "TB_TMG_APPLICATION": {
                        "C_REASON": {"type": "String", "value": reason},
                        "C_REMARK": {"type": "String", "value": reason},
                    },
                },
                "param": leave_type_id,
            },
        )
        return {
            "success": True,
            "employeeId": resolved_id,
            "virtualUserId": virtual_user_id,
            "requestId": str(result),
            "status": "submitted",
            "leaveType": leave_type,
            "startDate": start_date,
            "endDate": end_date,
            "period": period,
            "reason": reason,
        }

    def cancel(
        self,
        *,
        employee_id: str | None,
        request_id: str,
    ) -> dict[str, Any]:
        resolved_id, token, virtual_user_id = self._login(employee_id)
        result = self._rest(
            token,
            "cancelApplicationDTO",
            {"requestId": request_id},
        )
        return {
            "success": True,
            "employeeId": resolved_id,
            "virtualUserId": virtual_user_id,
            "requestId": str(result),
            "status": "cancelled",
        }

    def _login(self, employee_id: str | None) -> tuple[str, str, str]:
        resolved_id = (employee_id or self.http.settings.user_id).strip()
        if not resolved_id:
            raise MockSandboxError("缺少员工工号，无法登录请假沙箱")
        payload = self.http.request_json(
            "POST",
            "/mobile/otherApp/getTokenByParamThreeNew.do",
            form={
                "authCode": "",
                "clientId": "XiaoYuanAI",
                "userCode": resolved_id,
                "zoneCode": "mock-zone",
            },
        )
        token = str(payload.get("result") or "")
        if not payload.get("success") or not token:
            raise MockSandboxError("获取请假沙箱 Token 失败")
        return resolved_id, token, str(payload.get("userId") or "")

    def _rest(self, token: str, method: str, param: object) -> Any:
        payload = self.http.request_json(
            "POST",
            "/mobile/mobWebApp/getListDataForRestCode.do",
            form={
                "token": token,
                "restCode": "hcm",
                "selectMethod": method,
                "param": json.dumps(param, ensure_ascii=False),
            },
        )
        if str(payload.get("success")).lower() != "true":
            raise MockSandboxError(str(payload.get("reason") or "请假操作失败"))
        return payload.get("result")


def create_leave_tools(client: MockSandboxLeaveClient) -> list[BaseTool]:
    @tool(
        "queryLeaveBalance",
        args_schema=QueryLeaveInput,
        description="查询当前员工在请假沙箱中的年休假、事假类型和剩余天数。",
    )
    def query_leave_balance(employeeId: str | None = None) -> str:
        try:
            result = client.query(employeeId)
        except MockSandboxError as exc:
            result = {"success": False, "message": str(exc)}
        return json.dumps(result, ensure_ascii=False)

    @tool(
        "applyLeave",
        args_schema=ApplyLeaveInput,
        description=(
            "提交年休假或事假并扣减余额。必须先查询余额、收齐日期/时段/事由，"
            "并且用户已明确确认提交后才能调用。"
        ),
    )
    def apply_leave(
        leaveType: str,
        startDate: str,
        endDate: str,
        reason: str,
        confirmed: bool,
        employeeId: str | None = None,
        period: str = "全天",
    ) -> str:
        del confirmed
        try:
            result = client.apply(
                employee_id=employeeId,
                leave_type=leaveType,
                start_date=startDate,
                end_date=endDate,
                period=period,
                reason=reason,
            )
        except MockSandboxError as exc:
            result = {"success": False, "message": str(exc)}
        return json.dumps(result, ensure_ascii=False)

    @tool(
        "cancelLeave",
        args_schema=CancelLeaveInput,
        description="按requestId撤销请假申请。只有用户明确确认撤销时才能调用。",
    )
    def cancel_leave(
        requestId: str,
        confirmed: bool,
        employeeId: str | None = None,
    ) -> str:
        del confirmed
        try:
            result = client.cancel(
                employee_id=employeeId,
                request_id=requestId,
            )
        except MockSandboxError as exc:
            result = {"success": False, "message": str(exc)}
        return json.dumps(result, ensure_ascii=False)

    return [query_leave_balance, apply_leave, cancel_leave]


def _remaining_days(groups: object) -> str:
    if not isinstance(groups, list):
        return ""
    for group in groups:
        if not isinstance(group, dict):
            continue
        for item in group.get("infoItems") or []:
            if isinstance(item, dict) and (
                item.get("id") == "Vacations"
                or item.get("displayName") == "剩余天数"
            ):
                return str(item.get("value") or "")
    return ""
