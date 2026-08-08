# `people-directory` 找人 Skill 与 Tool

`people-directory` 向 Agent 暴露一个 `find_person` Tool。Tool 根据用户明确提供的人员定位条件，
只调用一次外部人员目录，再对返回候选执行确定性的本地字段过滤，最终返回同时满足全部条件的
人员事实。

```text
用户自然语言
  → Agent区分定位条件与待核对说法
  → 一次调用find_person，传入全部定位条件
  → Tool选择固定优先级中第一个非空主要条件
  → 调用一次外部宽匹配人员接口
  → 对同一份list[Person]依次执行全部非空条件filter
  → 返回共同匹配人员或no_common_match
  → Agent先陈述查询事实，再按需提供有限度的辅助比较
```

## 1. 职责边界

### Agent负责

- 判断何时需要找人；
- 从自然语言提取定位条件；
- 区分定位条件和用户希望核对的说法；
- 对纯数字等语义歧义进行追问；
- 根据Tool返回事实组织回答；
- 比较用户说法时使用“按当前查询结果看”等限定表达，不声称完成身份认证。

例如：

```text
用户：工号001849、手机尾号3987的人是不是张鹏飞？
定位条件：employee_id=001849、phone_suffix=3987
待核对说法：姓名是不是张鹏飞
```

调用Tool时不要传`name=张鹏飞`。Agent读取返回人员的真实姓名后再辅助比较。

### Tool负责

- 校验并规范化输入；
- 确保至少存在一个主要定位条件；
- 按固定优先级选择一个条件调用一次外部人员目录；
- 对外部宽匹配候选执行全部非空条件的本地字段过滤；
- 按工号去重；
- 返回同时满足全部条件的人员事实；
- 区分正常无共同匹配和外部服务异常。

Tool不负责判断用户说法、身份认证、相似人员推荐、追问用户或生成面向用户的回复。

### 外部目录客户端负责

- 调用外部宽匹配接口；
- 将外部字段映射成统一的`PersonInfo`；
- 返回候选列表，不判断候选因哪个字段命中。

## 2. Tool输入

```text
find_person(
    employee_id: string | null,
    phone: string | null,
    name: string | null,
    phone_suffix: string | null,
    department: string | null
)
```

| 参数 | 角色 | 本地过滤规则 |
|---|---|---|
| `employee_id` | 主要条件 | 工号规范化后完全相等 |
| `phone` | 主要条件 | 手机号去除常见分隔符后完全相等 |
| `name` | 主要条件 | 人员姓名字段包含输入文字 |
| `phone_suffix` | 主要条件 | 标准化手机号以输入数字结尾 |
| `department` | 附加条件 | 部门名称完全相等 |

输入规则：

- `employee_id`、`phone`、`name`、`phone_suffix`至少提供一个；
- `department`不能单独使用；
- 所有非空条件都是AND关系；
- 显式空字符串直接拒绝；
- 未定义字段直接拒绝；
- 1至5位纯数字工号左补零至6位；
- 手机号和尾号只接受数字及空格、`+()-`等常见分隔符；
- 姓名是字面包含匹配，不做拼音、同音字或错别字纠正。

## 3. 单次查询与过滤管道

主要条件使用固定优先级：

```text
employee_id > phone > name > phone_suffix
```

选择第一个非空条件的规范化值，只调用一次外部API。随后对同一候选列表按稳定顺序应用全部
非空条件：

```text
远程候选
  → 按employee_id精确过滤（如已传）
  → 按phone精确过滤（如已传）
  → 按name包含过滤（如已传）
  → 按phone_suffix结尾过滤（如已传）
  → 按department精确过滤（如已传）
  → 最终people
```

上游数字查询是跨字段子串召回。例如用工号搜索时，可能同时返回手机号包含该数字的员工；
`filter_by_employee_id`必须检查真实工号完全相等。手机尾号查询也可能返回工号或手机号中间包含
相同数字的候选；`filter_by_phone_suffix`必须检查真实手机号`endswith`。

姓名查询由上游按姓名字段召回，本地仍通过`query in person.name`验证。因此`name="涵"`可以
匹配“郑子涵”“郑若涵”，但不能匹配姓名中没有“涵”的候选。

## 4. Tool输出

匹配成功：

```json
{
  "people": [
    {
      "employee_id": "001849",
      "name": "王子涵",
      "phone": "13800003987",
      "email": "wangzihan@example.com",
      "department": "数字化部"
    }
  ],
  "error": null
}
```

没有人员同时满足全部条件：

```json
{
  "people": [],
  "error": null,
  "noMatch": {
    "reason": "no_common_match",
    "conditions": ["employee_id", "phone_suffix", "department"]
  }
}
```

外部人员服务异常：

```json
{
  "people": [],
  "error": {
    "code": "service_error"
  }
}
```

单次外部查询无法确定究竟是某个条件不存在，还是多个条件分别属于不同员工。因此Tool只返回
`no_common_match`，不得声称具体哪个条件错误。

## 5. 远程接口

```http
GET /api/eop-olk/api/v2/addressbook/search
```

```text
searchType=2
searchValue=<选中的主要条件规范化值>
page=1
size=100
```

字段映射：

| 远程字段 | `PersonInfo`字段 |
|---|---|
| `loginCode` | `employee_id` |
| `name` | `name` |
| `telPhone/telPhone1/telPhone2/workPhone` | `phone` |
| `email/mail/emailAddress` | `email` |
| `orgName/orgFullName` | `department` |

## 6. 验收清单

1. Agent只看到一个`find_person` Tool。
2. 部门不能作为唯一条件。
3. 多条件只调用一次外部人员接口。
4. 查询入口优先级固定为工号、完整手机号、姓名、手机尾号。
5. 所有非空条件都对同一候选列表执行本地AND过滤。
6. 工号只匹配真实工号字段，完整手机号只做完整相等，尾号只做`endswith`。
7. 姓名按人员姓名字段包含匹配。
8. 部门只做附加精确过滤。
9. 空结果只返回`no_common_match`，不推断具体错误条件。
10. 正常空结果与外部服务异常严格区分。

## 7. 源码位置

| 内容 | 文件 |
|---|---|
| Agent Skill | `app/features/people/skill.py` |
| Tool、过滤管道和目录客户端 | `app/features/people/tools.py` |
| Tool单元测试 | `tests/features/people/test_tools.py` |
| 远程映射测试 | `tests/integrations/test_mock_sandbox.py` |
