"""Meeting-room workflow skill backed by the two narrow execution tools."""

from __future__ import annotations

from agent_skill import AgentSkill
from meeting_room_tool import MeetingRoomClient, create_meeting_room_tools


MEETING_ROOM_SKILL_INSTRUCTIONS = """## 执行流程

### 1. 收集查询条件

- 用户提供时间、楼层、房间名称/编号中的任一线索时，都调用queryMeetingRooms；
  不要求用户必须先提供楼层。
- floor、room、date、timeRange只传用户已明确表达或可由服务端时间确定的值。
- 用户只提供楼层时，查询该楼层全部房间及当天日程。
- 用户只提供房间时，查询该房间当天09:00-18:00的占用和空闲时间。
- 用户只提供时间时，查询全部楼层，并按真实结果推荐该时段可用的房间。
- 用户提供日期但未提供时间时，展示当天房间日程和连续空闲时段。
- 查询指定时段且capacity未提供时按5人处理。
- 用户明确说“今天”“明天”或“后天”时，根据本轮服务端时间上下文换算date。
- 用户只说“现在”但没有给出结束时间或时长时，先询问完整时段。
- queryMeetingRooms只查询，不创建预约。

### 2. 根据查询结果回答

- 所有房间状态和时间建议必须来自queryMeetingRooms，不凭常识或历史消息推测。
- 用户问时间时，优先给出该时间可用的房间；没有合适房间时，使用返回的
  suggestedTimeRanges推荐相同时长的替代时间。
- 用户问楼层时，按房间列出占用情况和availableTimeRanges。
- 用户问房间时，说明该房间09:00-18:00的占用情况和适合的空闲时间。
- 只展示queryMeetingRooms真实返回的候选，不编造房间、状态、时间或roomId。

### 3. 让用户选择

- 预约必须使用查询结果中的roomId；候选不唯一时，请用户选择具体会议室。
- 用户选择会议室只代表选中候选，不等于确认预约，此时不得调用bookMeetingRoom。

### 4. 获取明确确认

- 在预约前汇总会议室、楼层、日期、时间、人数和主题，请用户明确确认。
- 用户明确提供主题时原样使用；用户未提供主题时，不猜测用户名或主题，也不为此追加
  追问。确认摘要中说明“主题将由服务端按‘预约人姓名+预约的会议’生成”，调用
  bookMeetingRoom时将theme留空。
- 只有用户明确表示“确认预约”等同等含义时，confirmed才可设为true。
- 如果确认后任何预约参数发生变化，必须基于新参数重新获取确认。
- 不允许猜测roomId、floor、date、timeRange或confirmed。

### 5. 执行预约

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
