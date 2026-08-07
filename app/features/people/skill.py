"""Agent policy for factual employee lookup."""

from __future__ import annotations

from app.agent.skill import AgentSkill
from app.features.people.tools import PeopleDirectory, create_people_search_tools


PEOPLE_SKILL_INSTRUCTIONS = """## 人员信息查询

- 当用户要找员工、了解人员信息、核对身份线索，或询问联系方式、邮箱、部门等信息时，使用
  `find_person` 获取人员事实。你负责结合用户的完整问题和返回结果判断、比较、解释或追问。
- 自行区分“用于查询的条件”和“希望从结果中核对的信息”。例如“工号 001849、手机尾号
  3987 的人是不是张鹏飞”，用工号和手机尾号查询，再查看结果中的姓名；不要把“张鹏飞”也
  作为筛选条件，否则会丢失用于判断的实际姓名。
- 根据问题灵活使用返回的人员信息。候选不充分或含义不明确时，可以展示必要候选、说明不确定性
  或继续追问；不要暴露回答当前问题不需要的信息。
- `people`为空且`noMatch.reason=condition_not_found`时，只说明`conditions`中的条件没有匹配；
  `conditions_conflict`表示各条件分别能找到人、但不属于同一员工，应指出线索不一致并请用户
  核对。不要擅自删除某个条件后重新查询，也不要把服务异常说成“查无此人”。
- 用户显式给出空值、格式错误或无法判断含义的线索时，不要用空字符串调用Tool；能从原话唯一
  修正格式时再查询，否则只追问缺失或歧义的必要信息。
- 用户只给出无语义纯数字，例如“3987 是谁”，且无法从上下文判断含义时，先询问这是工号
  还是手机号尾号不调工具；用户明确说“工号 1849”时直接查询。
  始终以 Tool 返回事实为依据，不编造人员信息。
"""


def create_people_skill(directory: PeopleDirectory) -> AgentSkill:
    """Bind the employee lookup policy and Tool as one capability."""
    return AgentSkill(
        name="people-directory",
        description="查询一个或多个人员条件，获得员工事实，并由你结合用户问题灵活判断和回答。",
        instructions=PEOPLE_SKILL_INSTRUCTIONS,
        tools=create_people_search_tools(directory),
    )
