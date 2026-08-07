"""Meeting-room capability binding search and human-confirmed booking tools."""

from __future__ import annotations

from app.agent.skill import AgentSkill
from app.features.meeting_room.tools import (
    MeetingRoomClient,
    create_meeting_room_tools,
)


MEETING_ROOM_SKILL_INSTRUCTIONS = """## 对话策略

- 楼层是查询工具唯一必填的业务条件；无法从用户表述推断时才追问。已知楼层后是否查询
  由你自行判断，缺少其他条件不代表参数不完整。
- date是可修改的查询和预约日期，省略时默认今天。查询静态信息时省略time；查询今天完整
  时间表时给`search_meeting_rooms.time`传空对象。会议室每天可用时段为09:00-18:30。
- 时长默认60分钟，人数默认5人。用户未主动说明时直接采用，禁止追问“预订多长时间”或
  “多少人参加”；只给开始时间时自动加60分钟。
- 房间号最后两位是序号、前缀是楼层：808会议室在8楼，1101会议室在11楼。直接推断，
  不要为了判断楼层调用工具。该规则只适用于会议室名称或房间号，不适用于外部roomId。
- 用户同时给出的楼层与房间号前缀冲突时，例如“8楼的606”，不要自行选择8楼或6楼，
  也不要调用Tool尝试两种解释；先请用户确认楼层或房间号。外部roomId不能用于推断楼层。

## 工作流

- 所有只读查询统一调用search_meeting_rooms。已知房间名称或房间号时传roomQuery；容量、
  设备和只看空闲等条件放进requirements；日期和时段放进time。Tool会在一次楼层查询中
  逐层过滤并返回交集，不要拆成多次调用，也不要自行按roomId交叉。
- 用户询问指定房间是否可用时不要传availableOnly，必须保留该房间并读取isAvailable和
  conflicts；用户要求“找空闲会议室”时才传availableOnly=true。容量筛选默认按5人，
  用户主动说明人数时覆盖默认值。
- 多楼层可并行查询，一轮最多查询5个楼层；更多时请用户缩小范围。
- `matchedRoomCount=0`时按`emptyReason`回答：区分楼层无会议室、该楼层没有指定房间、容量
  不足、缺少设备和指定时段无空闲。不要擅自删除房间、楼层、时间或requirements后扩大查询；
  服务异常也不能说成“没有会议室”。
- 用户显式给出空楼层、空房间名、空设备名或错误时间格式时，不要用空值调用Tool；只有能从
  原话唯一修正时才修正，否则追问完成查询必需的最少信息。
- 根据查询结果自行选择合适、简短、精炼的说法，让用户看清可用性并自行选择；除非用户
  明确要求推荐，否则不要替用户选择房间或时段。
- 始终围绕用户的原始问题裁剪房间和字段：明确指定房间时只回答该房间；只问日程时不附加
  容量或设备。除非用户要求比较或查看备选，否则不展示同楼层其他房间。
- 候选必须展示roomName和roomId。选定准确roomId与时间后调用book_meeting_room；冲突时
  告知占用方和占用时间，空闲时展示返回的预约卡片。
- 推送卡片不代表预约成功；真实预约由用户在卡片中确认，不属于模型工具能力。不要用
  文字或Markdown模拟卡片，不展示调用过程，失败时如实说明。
"""


def create_meeting_room_skill(client: MeetingRoomClient) -> AgentSkill:
    """Bind the meeting-room policy and execution tools as one capability."""
    return AgentSkill(
        name="meeting-room-assistant",
        description="查询会议室静态信息和单日安排，并创建待确认预约卡片。",
        instructions=MEETING_ROOM_SKILL_INSTRUCTIONS,
        tools=tuple(create_meeting_room_tools(client)),
    )
