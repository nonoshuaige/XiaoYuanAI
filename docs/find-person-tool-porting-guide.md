# `find_person` 远程通讯录治理 Tool

本文不依赖任何 Agent 框架，只定义一个 Tool：`find_person`。

完整链路：

```text
用户自然语言
  → LLM 识别找人意图并提取全部明确线索
  → 调用 find_person
  → Tool 选择一个主查询字段
  → Tool 调用单字段远程 API
  → Tool 做精确匹配、本地校验和必要的冲突扩查
  → Tool 返回结构化结果
  → LLM 根据结果回答用户
```

## 1. 职责边界

### LLM 负责

1. 判断用户是否明确要求找人、确认员工或查询联系方式；
2. 从自然语言提取用户明确提供的全部参数；
3. 调用一次 `find_person` Tool；
4. 根据 Tool 的结构化结果回答用户。

LLM 不负责：

- 决定先按哪个字段查；
- 自己拆成多次 Tool 调用；
- 自己判断线索是否冲突；
- 自己选择冲突候选；
- 猜测远程通讯录结果。

### Tool 负责

1. 清洗和校验参数；
2. 按固定优先级选择主查询字段；
3. 把一个字段和值传给远程 API；
4. 对远程候选做本地精确匹配；
5. 用主结果在本地核对其他线索；
6. 只对不一致的手机号或姓名扩查；
7. 汇总真实候选、冲突来源和空扩查状态；
8. 返回统一结构。

### 远程 API 负责

远程 API 每次只接收一种搜索值：

```text
工号 / 手机号 / 姓名，三选一
```

它不负责多线索一致性校验，也不负责冲突治理。

## 2. Tool 输入

```text
find_person(
    employee_id: string | null,
    phone: string | null,
    name: string | null,
    department: string | null
)
```

| 字段 | 含义 | 能否触发远程查询 |
|---|---|---|
| `employee_id` | 工号 | 可以 |
| `phone` | 手机号 | 可以 |
| `name` | 姓名 | 可以 |
| `department` | 部门辅助线索 | 不可以，只做本地校验 |

规则：

- LLM 必须把用户明确提供的全部线索一次性传入；
- 工号、手机号、姓名至少提供一个；
- 所有字符串执行 `strip()`；
- 空字符串按未提供处理；
- 部门不能单独调用找人工具。

示例：

```text
用户：工号000072的张三手机号多少
Tool 参数：employee_id="000072", name="张三"
```

```text
用户：找一下手机号13800000135的王一鸣
Tool 参数：phone="13800000135", name="王一鸣"
```

## 3. Tool description

下面是提供给 LLM 的完整 Tool description：

```text
查询员工人事信息，可返回工号、姓名、手机号、部门等。用户明确要求找人、确认员工或
查询员工信息时使用。调用前至少需要工号、手机号或姓名之一；如果都没有，先引导用户补充。
调用时传入用户明确提供的全部线索。若返回 conflicting_candidates，必须展示全部 people
及匹配来源并请用户确认，不得自行选择。若 expanded_searches 中某字段 status=not_found，
表示该线索已经查询但未命中，应告知用户核对，不要用相同线索重复查询。department 仅用于
辅助校验，不能单独调用本工具。
```

参数描述：

```text
employee_id：用户明确提供的工号；未提供时留空。
phone：用户明确提供的手机号；未提供时留空。
name：用户明确提供的姓名；未提供时留空。
department：用户明确提供的辅助部门线索；未提供时留空。
```

## 4. 远程 API

### 请求

```http
GET /api/eop-olk/api/v2/addressbook/search
```

```text
Query:
  searchType=2
  searchValue=<工号、手机号或姓名中的一个值>
  page=1
  size=100

Headers:
  Accept: application/json
  X-LOGINCODE: <当前登录用户工号>
  SYSID: 2
```

例如按工号查询：

```text
searchValue=000072
```

例如按姓名扩查：

```text
searchValue=张三
```

### 响应

员工数组位于：

```text
data.user
```

成功业务码：

```text
code=0
```

### 字段映射

| 远程字段 | Tool 字段 | 规则 |
|---|---|---|
| `loginCode` | `employee_id` | 转为字符串 |
| `name` | `name` | 转为字符串 |
| `telPhone` / `telPhone1` / `telPhone2` / `workPhone` | `phone` | 取第一个非空值 |
| `orgName` / `orgFullName` | `department` | 优先 `orgName` |

远程接口可能返回模糊候选。Tool 必须在本地执行完全相等判断：

```text
person[查询字段] == 查询值
```

只有精确相等的员工才算命中。

## 5. Tool 核心逻辑

### 固定优先级

```text
employee_id > phone > name
```

Tool 从用户已经提供的身份字段中选择优先级最高的一个发起第一次查询。

### 执行逻辑

```python
identity_clues = {
    "employee_id": employee_id,
    "phone": phone,
    "name": name,
}

primary_field = first_provided(
    "employee_id",
    "phone",
    "name",
)

primary_people = exact_search(
    field=primary_field,
    value=identity_clues[primary_field],
)

if not primary_people:
    return not_found(primary_field)

remaining_clues = all_provided_clues_except(primary_field)

fully_matching_people = [
    person
    for person in primary_people
    if all(person[field] == value for field, value in remaining_clues)
]

if fully_matching_people:
    return found_or_multiple(fully_matching_people)

conflicting_fields = [
    field
    for field, value in remaining_clues
    if primary_result_does_not_match(field, value)
]

candidates = mark(primary_people, matched_by=primary_field)
expanded_searches = {}

for field in conflicting_fields:
    if field == "department":
        continue

    matches = exact_search(
        field=field,
        value=identity_clues[field],
    )

    candidates += mark(matches, matched_by=field)
    expanded_searches[field] = {
        "query_value": identity_clues[field],
        "status": "found" if matches else "not_found",
    }

return conflicting_candidates(
    people=candidates,
    conflicting_fields=conflicting_fields,
    expanded_searches=expanded_searches,
)
```

### 必须遵守的约束

- 全部线索一致：只调用一次远程 API；
- 姓名一致：不按姓名重复查询；
- 手机号一致：不按手机号重复查询；
- 只有姓名不一致：只按姓名扩查一次；
- 只有手机号不一致：只按手机号扩查一次；
- 姓名和手机号都不一致：分别扩查一次；
- 部门不一致：标记冲突，但不调用远程 API；
- 工号是最高优先级，不额外做工号扩查；
- `people` 只放真实员工，不放空占位；
- 扩查为空只记录最小状态：`query_value + status=not_found`。

## 6. Tool 返回结构

```json
{
  "status": "found | multiple_matches | not_found | conflicting_candidates | invalid_input | service_error",
  "primary_field": "employee_id | phone | name | null",
  "matched_by": "employee_id | phone | name | null",
  "checked_fields": [],
  "conflicting_fields": [],
  "expanded_searches": {},
  "people": [],
  "message": "",
  "source": "mock-sandbox"
}
```

| 字段 | 含义 |
|---|---|
| `status` | Tool 最终业务状态 |
| `primary_field` | 第一次远程查询使用的字段 |
| `matched_by` | 与 `primary_field` 相同，保留兼容性 |
| `checked_fields` | 用户实际提供并参与检查的字段 |
| `conflicting_fields` | 与主结果不一致的字段 |
| `expanded_searches` | 对冲突字段进行扩查的结果 |
| `people` | 全部真实候选员工 |
| `message` | 给 LLM 的直接业务结论 |
| `source` | 数据来源 |

### 状态说明

| `status` | 含义 |
|---|---|
| `found` | 唯一员工命中，全部线索一致 |
| `multiple_matches` | 多位员工满足线索，需要用户确认 |
| `not_found` | 主查询字段没有精确命中 |
| `conflicting_candidates` | 主结果与其他线索冲突 |
| `invalid_input` | 工号、手机号、姓名全部为空 |
| `service_error` | 远程 API 或响应格式异常 |

## 7. 两个关键结果示例

### 工号和姓名冲突，姓名扩查为空

输入：

```json
{
  "employee_id": "000072",
  "name": "张三"
}
```

输出：

```json
{
  "status": "conflicting_candidates",
  "primary_field": "employee_id",
  "matched_by": "employee_id",
  "checked_fields": ["employee_id", "name"],
  "conflicting_fields": ["name"],
  "expanded_searches": {
    "name": {
      "query_value": "张三",
      "status": "not_found"
    }
  },
  "people": [
    {
      "employee_id": "000072",
      "name": "王一鸣",
      "phone": "13800000135",
      "department": "数据信息部",
      "matched_by": "employee_id"
    }
  ],
  "message": "工号命中的员工与姓名线索不一致。已按姓名“张三”扩查，但未找到精确匹配员工。请确认目标人员。",
  "source": "mock-sandbox"
}
```

LLM 应回答：

```text
工号000072命中王一鸣；姓名“张三”已经扩查，但没有找到精确匹配员工。
请重新核对工号或姓名并确认目标人员。
```

LLM 不应回答：

```text
如果要找张三，请只告诉我姓名，我再查询一次。
```

### 只有工号，唯一命中

输入：

```json
{
  "employee_id": "000072"
}
```

输出：

```json
{
  "status": "found",
  "primary_field": "employee_id",
  "matched_by": "employee_id",
  "checked_fields": ["employee_id"],
  "conflicting_fields": [],
  "expanded_searches": {},
  "people": [
    {
      "employee_id": "000072",
      "name": "王一鸣",
      "phone": "13800000135",
      "department": "数据信息部"
    }
  ],
  "message": "已从外部通讯录查询并验证全部线索，找到 1 位员工。",
  "source": "mock-sandbox"
}
```

## 8. LLM 读取 Tool 结果的规则

```text
status=found
  → 返回员工信息。

status=multiple_matches
  → 展示全部 people，请用户确认。

status=conflicting_candidates
  → 展示全部 people 和 matched_by，不得自行选择。
  → 如果 expanded_searches 某字段 status=not_found，说明该字段已经查过但未命中，
    请用户核对线索，不要重复查询相同字段和值。

status=not_found
  → 说明主查询没有精确命中。

status=invalid_input
  → 请用户补充工号、手机号或姓名之一。

status=service_error
  → 说明通讯录服务异常，不得编造员工信息。
```

## 9. 最小验收清单

1. LLM 会从自然语言一次性提取全部明确线索；
2. LLM 只调用一次 `find_person`，不自己拆查询；
3. Tool 固定使用“工号 > 手机号 > 姓名”；
4. 每次远程 API 调用只携带一个搜索值；
5. 远程模糊候选会被本地精确过滤；
6. 全部线索一致时不扩查；
7. 只扩查不一致的手机号或姓名；
8. 部门永远不触发远程查询；
9. 扩查为空时记录 `status=not_found`；
10. `people` 只包含真实员工；
11. 冲突候选全部包含 `matched_by`；
12. LLM 根据结构化状态回答，不猜测、不重复查询。

## 10. 当前源码位置

| 内容 | 文件 |
|---|---|
| 远程 HTTP Client | `app/integrations/mock_sandbox/client.py` |
| Tool 输入、治理逻辑和 description | `app/features/people/tools.py` |
| 远程查询与冲突测试 | `tests/integrations/test_mock_sandbox.py` |
| Tool 参数和调用测试 | `tests/features/people/test_tools.py` |
