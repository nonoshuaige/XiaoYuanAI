"""Meeting-room workflow skill backed by the two narrow execution tools."""

from __future__ import annotations

from app.agent.skill import AgentSkill
from app.features.meeting_room.tools import (
    MeetingRoomClient,
    create_meeting_room_tools,
)


MEETING_ROOM_SKILL_INSTRUCTIONS = """## 工具边界

- 使用searchMeetingRooms查询会议室和预约日程；房间、状态、时间和roomId只以工具结果为准。
- 有楼层就传floor，没有楼层也可以按房间、日期或时间查询全部楼层。相对日期根据本轮
  服务端时间换算；只给开始时间时默认持续1小时，指定时段未给人数时使用默认值5。
- 候选不唯一时让用户选择。确定roomId、floor、date和timeRange后，调用
  pushMeetingRoomBookingForm推送预约单；theme未提供时留空，由服务端使用鉴权userName生成
  “{userName}预定的会议”。
- pushMeetingRoomBookingForm只推送预约单。真实预约、修改和确认由预约单界面及服务端处理，
  不属于模型工具能力。
- 不猜测参数，不用文字或Markdown模拟预约单；工具失败时如实说明。
"""


def create_meeting_room_skill(client: MeetingRoomClient) -> AgentSkill:
    """Bind the meeting-room policy and execution tools as one capability."""
    return AgentSkill(
        name="meeting-room-booking",
        description=(
            "查询会议室并推送可编辑的待确认预约单。"
        ),
        instructions=MEETING_ROOM_SKILL_INSTRUCTIONS,
        tools=tuple(create_meeting_room_tools(client)),
    )
