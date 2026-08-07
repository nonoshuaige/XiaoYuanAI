# `people-directory` 找人 Skill 与 Tool

`people-directory` 向 Agent 暴露一个 `find_person` Tool。Tool 接收一个或多个人员查询条件，
调用对应的底层 `find_by_*`，并返回所有条件交集中的人员事实。

```text
用户自然语言
  → Agent 区分查询条件与待核对说法
  → 一次调用 find_person，传入全部查询条件
  → 每个非空条件调用对应 find_by_*
  → 各 finder 对远程宽匹配结果做字段级过滤
  → Tool 按 employee_id 对各集合取交集
  → 返回人员事实；空交集时返回精简原因
  → Agent 阅读事实并回答用户
```

## 1. 职责边界

### Agent 负责

- 提取用户用于定位人员的明确条件；
- 区分查询条件和等待核对的说法；
- 把全部查询条件放在同一次 Tool 调用中；
- 根据返回的 `people` 回答用户；
- 对无语义纯数字先追问按工号还是手机号尾号查询。

例如：

```text
用户：工号001849、手机尾号3987的人是不是张鹏飞？
查询条件：employee_id=001849、phone_suffix=3987
待核对说法：姓名是不是张鹏飞
```

调用 Tool 时不要传 `name=张鹏飞`。Agent 在 Tool 返回后读取 `PersonInfo.name` 进行比较。

### Tool 负责

- 校验并规范化查询参数；
- 为每个非空参数调用对应底层 finder；
- 对底层 API 的宽匹配结果做正确字段过滤；
- 按稳定的 `employee_id` 去重并取集合交集；
- 只返回同时满足所有查询条件的 `people`；
- 把“条件无匹配”“条件指向不同人员”和上游服务异常区分开。

Tool 不负责：

- 判断用户的说法是否正确；
- 选择最相似人员；
- 给人员打分；
- 生成“确认成功”“信息冲突”等业务结论；
- 生成面向用户的回复。

### 底层 finder 负责

每种查询条件由一个独立 finder 实现，统一返回人员集合：

| Tool 参数 | 底层函数 | 字段过滤规则 |
|---|---|---|
| `employee_id` | `find_by_employee_id` | 工号补零后完全相等 |
| `phone` | `find_by_phone` | 标准化后的手机号完全相等 |
| `phone_suffix` | `find_by_phone_suffix` | 标准化后的手机号以后缀结尾 |
| `name` | `find_by_name` | 完整姓名完全相等 |
| `name_fragment` | `find_by_name_fragment` | 姓名包含指定片段 |

新增查询条件时增加新的 finder，再接入统一 Tool；不要把新的查询方式直接暴露成新的 Agent
Tool。每个 finder 可以独立测试，Tool 的返回协议保持不变。

## 2. Tool 输入

```text
find_person(
    employee_id: string | null,
    phone: string | null,
    phone_suffix: string | null,
    name: string | null,
    name_fragment: string | null
)
```

规则：

- 至少提供一个条件；
- 所有非空条件都是 AND 关系；
- 未提供的字段不参与查询；
- 显式空字符串不是“未提供”，Schema 直接拒绝；
- 1 至 5 位纯数字工号左补零至 6 位；
- 手机号和尾号只接受数字及空格、`+()-` 等常见分隔符；
- `phone_suffix` 仅在用户明确表达手机尾号或手机号后几位时使用；
- `name_fragment` 仅在用户明确表示姓名不完整时使用；
- 无语义纯数字不得由 Tool 猜测查询类型。

## 3. Tool 输出

```json
{
  "people": [
    {
      "employee_id": "001849",
      "name": "王鹏飞",
      "phone": "13800003987",
      "email": "wangpengfei@example.com",
      "department": "软件研发中心"
    }
  ],
  "error": null
}
```

输出语义：

- `people` 中每个人都满足全部传入条件；
- `people=[]` 表示没有人同时满足全部传入条件；
- `noMatch.reason=condition_not_found` 表示 `conditions` 中至少一个条件自身没有匹配；
- `noMatch.reason=conditions_conflict` 表示各条件分别有人匹配，但没有共同员工；
- `error=null` 表示查询正常完成；
- `error.code=service_error` 表示外部人员服务异常，此时不能把空列表解释为无人匹配；
- Tool 不返回 `status`、`outcome`、`message`、匹配分或身份核验结论。

## 4. 多条件示例

用户：

```text
工号1849、手机尾号3987的人是不是张鹏飞？
```

Agent 调用：

```json
{
  "employee_id": "1849",
  "phone_suffix": "3987"
}
```

Tool 内部：

```text
find_by_employee_id("1849")
  → 查询001849
  → 只保留employee_id == 001849
  → {P001}

find_by_phone_suffix("3987")
  → 查询3987
  → 只保留phone以3987结尾
  → {P001, P002}

交集
  → {P001}
```

Tool 返回 P001 的 `PersonInfo`，Agent 再比较其 `name` 是否为张鹏飞。

如果两个 finder 的结果不相交，Tool 返回：

```json
{
  "people": [],
  "error": null,
  "noMatch": {
    "reason": "conditions_conflict",
    "conditions": ["employee_id", "phone_suffix"]
  }
}
```

Agent 应说明两条线索指向不同人员并请用户核对，不能回答“此人不是张鹏飞”，也不能擅自
删除某个条件后扩大查询。如果尾号条件自身无匹配，则 `reason=condition_not_found` 且
`conditions=["phone_suffix"]`。

## 5. 远程接口

所有 finder 共用同一个宽匹配接口：

```http
GET /api/eop-olk/api/v2/addressbook/search
```

```text
searchType=2
searchValue=<finder规范化后的查询值>
page=1
size=100
```

远程接口可能因为工号、手机号或姓名任一位置相关而返回人员。finder 必须读取结构化人员字段
执行自己的严格过滤，不能根据接口返回顺序或命中位置判断。

员工映射字段：

| 远程字段 | `PersonInfo` 字段 |
|---|---|
| `loginCode` | `employee_id` |
| `name` | `name` |
| `telPhone/telPhone1/telPhone2/workPhone` | `phone` |
| `email/mail/emailAddress` | `email` |
| `orgName/orgFullName` | `department` |

## 6. 验收清单

1. Agent 只看到一个 `find_person` Tool。
2. Tool 至少需要一个查询条件，支持一次传入多个条件。
3. 每个已传条件调用对应的底层 finder。
4. 工号、完整手机号、手机尾号、完整姓名和姓名片段分别检查正确的人员字段。
5. 多个 finder 结果按 `employee_id` 取交集。
6. Tool 返回交集中的 `PersonInfo`；空结果只附带确定性原因，不生成面向用户的业务判断。
7. “是不是某人”中的姓名默认留给 Agent 对返回结果进行比较，不作为过滤条件。
8. 条件无匹配、条件互相冲突与外部服务错误严格区分。

## 7. 源码位置

| 内容 | 文件 |
|---|---|
| Agent Skill | `app/features/people/skill.py` |
| 统一 Tool、底层 finder 和目录客户端 | `app/features/people/tools.py` |
| Tool 单元测试 | `tests/features/people/test_tools.py` |
| 远程映射测试 | `tests/integrations/test_mock_sandbox.py` |
