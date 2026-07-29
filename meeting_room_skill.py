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
- 5人是服务端统一的默认参会人数；用户未说明人数时直接使用该默认值，不要声称是
  从用户话语中推断出来的。
- 用户明确说“今天”“明天”或“后天”时，根据本轮服务端时间上下文换算date。
- 用户只说“现在”但没有给出结束时间或时长时，先询问完整时段。
- 预约时间只能落在09:00-18:00，开始和结束必须是整点或半点；不得推荐或提交
  已经过去的日期、时间以及非工作时段。
- queryMeetingRooms只查询，不创建预约。

### 2. 根据查询结果回答

- 所有房间状态和时间建议必须来自queryMeetingRooms，不凭常识或历史消息推测。
- 用户问时间时，优先给出该时间可用的房间；没有合适房间时，使用返回的
  suggestedTimeRanges推荐相同时长的替代时间。
- 用户问楼层时，按房间列出占用情况和availableTimeRanges。
- 用户问房间时，说明该房间09:00-18:00的占用情况和适合的空闲时间。
- 查询今天的日程时，只使用工具返回的displayWindow、timeline和occupied描述当前
  半小时槽及之后的安排；当前半小时槽之前的历史预约不再复述。
- 只展示queryMeetingRooms真实返回的候选，不编造房间、状态、时间或roomId。

### 3. 让用户选择并生成预约卡片

- 预约必须使用查询结果中的roomId；候选不唯一时，请用户选择具体会议室。
- 用户选择具体会议室，且roomId、floor、date和timeRange齐全后，调用bookMeetingRoom
  生成待确认预约卡片；该工具只生成草稿，绝不创建预约。
- 成功生成卡片后，不在普通回复中重复会议室、日期、时间、人数和主题，参数只在卡片中
  展示和修改。
- 用户明确提供主题时原样使用；用户未提供主题时，不猜测用户名或主题，也不为此追加
  追问。说明“主题将由服务端按‘预约人姓名+预约的会议’生成”，调用
  bookMeetingRoom时将theme留空。
- 不允许猜测roomId、floor、date或timeRange。

### 4. 最终确认必须由用户操作

- 即使用户在自然语言中说“确认”“就订这个”，模型也不能代替用户执行最终预约；
  必须提示用户在预约卡片中检查、修改参数并点击“确认预约”。
- 用户修改卡片参数后，由服务端重新校验时间、容量与冲突，不需要模型补发确认。
- 只有用户点击卡片确认按钮后，服务端才会再次查询冲突并调用真实预约写入。
- 在卡片返回真实bookingId和meetingId之前，不得声称预约成功或生成成功凭证。
- 卡片确认失败时说明真实错误，不得擅自改订其他会议室。
"""


def create_meeting_room_skill(client: MeetingRoomClient) -> AgentSkill:
    """Bind the meeting-room policy and execution tools as one capability."""
    return AgentSkill(
        name="meeting-room-booking",
        description=(
            "查询会议室、生成可编辑预约卡片，并由用户在卡片中最终确认。"
        ),
        instructions=MEETING_ROOM_SKILL_INSTRUCTIONS,
        tools=tuple(create_meeting_room_tools(client)),
    )
