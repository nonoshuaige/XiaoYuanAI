"""Meeting-room workflow skill backed by the two narrow execution tools."""

from __future__ import annotations

from agent_skill import AgentSkill
from meeting_room_tool import MeetingRoomClient, create_meeting_room_tools


MEETING_ROOM_SKILL_INSTRUCTIONS = """## 执行流程

### 1. 收集查询条件

- floor必填，只能使用用户明确提供的纯数字楼层，不允许猜测。
- 用户只提供floor时，调用queryMeetingRooms查询该楼层全部会议室。
- 查询指定时段时，date和timeRange必须同时提供；capacity未提供时按5人处理。
- 用户明确说“今天”“明天”或“后天”时，根据本轮服务端时间上下文换算date。
- 用户只说“现在”但没有给出结束时间或时长时，先询问完整时段。
- queryMeetingRooms只查询，不创建预约。

### 2. 让用户选择

- 只展示queryMeetingRooms真实返回的候选，不编造房间、可用状态或roomId。
- 预约必须使用查询结果中的roomId；候选不唯一时，请用户选择具体会议室。
- 用户选择会议室只代表选中候选，不等于确认预约，此时不得调用bookMeetingRoom。

### 3. 获取明确确认

- 在预约前汇总会议室、楼层、日期、时间、人数和可选主题，请用户明确确认。
- 只有用户明确表示“确认预约”等同等含义时，confirmed才可设为true。
- 如果确认后任何预约参数发生变化，必须基于新参数重新获取确认。
- 不允许猜测roomId、floor、date、timeRange或confirmed。

### 4. 执行预约

- bookMeetingRoom必须同时提供roomId、floor、date、timeRange和confirmed=true。
- bookMeetingRoom会在创建前重新查询会议室并检查冲突；不得绕过重查。
- 只依据bookMeetingRoom的真实返回告知成功或失败，不得由模型生成成功凭证。
- 失败时不得声称成功，也不得擅自改订其他会议室。
"""


def create_meeting_room_skill(client: MeetingRoomClient) -> AgentSkill:
    """Bind the meeting-room policy and execution tools as one capability."""
    return AgentSkill(
        name="meeting-room-booking",
        description=(
            "查询会议室、引导用户选择具体房间，并在明确确认后创建预约。"
        ),
        instructions=MEETING_ROOM_SKILL_INSTRUCTIONS,
        tools=tuple(create_meeting_room_tools(client)),
    )
