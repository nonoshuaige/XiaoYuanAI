"""Agent policy for factual employee lookup."""

from __future__ import annotations

from app.agent.skill import AgentSkill
from app.features.people.tools import PeopleDirectory, create_people_search_tools


PEOPLE_SKILL_INSTRUCTIONS = """## 人员信息查询

- 当用户要找员工、了解人员信息、核对身份线索，或询问联系方式、邮箱、部门等信息时，使用
  `find_person` 获取人员事实。工号、完整手机号、姓名和手机尾号是主要定位条件；部门只能
  作为附加定位条件，不能单独用于查询。
- 姓名参数按姓名字段包含关系查询。例如用户明确要找姓名中包含“涵”的员工时，传
  `name="涵"`，Tool可以返回“子涵”“若涵”等姓名中包含该文字的候选。不要自行进行拼音、
  同音字或错别字纠正。
- 自行区分“用于查询的条件”和“希望从结果中核对的信息”。例如“工号 001849、手机尾号
  3987 的人是不是张鹏飞”，用工号和手机尾号查询，再查看结果中的姓名；不要把“张鹏飞”也
  作为筛选条件，否则会丢失用于辅助比较的实际姓名。“工号001849的人是数字化部的吗”同理，
  只用工号查询；“帮我找数字化部的张伟”中的部门才是附加定位条件。
- 用户提供多个定位条件时，把全部定位条件放在同一次Tool调用中。Tool负责所有非空条件的AND
  过滤；不要拆成多次查询，也不要自行合并候选。
- 回答时先说明根据哪些条件查询，再陈述Tool返回的人员事实。用户要求核对说法时，可以基于
  返回字段进行辅助比较，但使用“按当前查询结果看”“与您提供的信息一致/不一致”等限定表达，
  不声称完成正式身份认证。
- 根据问题灵活使用返回的人员信息。候选不充分或含义不明确时，可以展示必要候选、说明不确定性
  或继续追问；不要暴露回答当前问题不需要的信息。
- `people`为空且`noMatch.reason=no_common_match`时，只说明没有员工同时满足`conditions`中的
  全部条件并请用户核对；不要断言具体哪个条件错误，也不要擅自删除某个条件后重新查询。
  `error.code=service_error`表示人员服务异常，不能说成“查无此人”。
- 用户显式给出空值、格式错误或无法判断含义的线索时，不要用空字符串调用Tool；能从原话唯一
  修正格式时再查询，否则只追问缺失或歧义的必要信息。
- 用户只提供部门时，不调用Tool；请其补充工号、完整手机号、姓名或手机尾号中的至少一个。
- 用户只给出无语义纯数字，例如“3987 是谁”，且无法从上下文判断含义时，先询问这是工号
  还是手机号尾号不调工具；用户明确说“工号 1849”时直接查询。
  始终以 Tool 返回事实为依据，不编造人员信息。
"""


def create_people_skill(directory: PeopleDirectory) -> AgentSkill:
    """Bind the employee lookup policy and Tool as one capability."""
    return AgentSkill(
        name="people-directory",
        description="根据一个或多个人员定位条件查找员工事实，并由你结合用户问题谨慎说明结果。",
        instructions=PEOPLE_SKILL_INSTRUCTIONS,
        tools=create_people_search_tools(directory),
    )
