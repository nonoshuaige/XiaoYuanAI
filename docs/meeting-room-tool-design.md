# 会议室 Skill —— 查询过滤管道与预约草稿

会议室能力围绕外部 `selectMeet(floor)` 展开。上游一次返回单楼层全部会议室和预约信息，
Agent 对外只暴露一个只读查询 Tool 和一个预约草稿 Tool。

## 1. 外部原语 API

```text
POST /oca/ibpmeetrese/q/selectMeet
Body:  {"address": "<floor>"}
Auth:  Userauthorization: <token>
```

接口返回房间静态信息和未来预约单。房间名称是用户侧标识，`roomId` 是外部系统身份；
查询 Tool 在一次楼层响应内完成名称到 `roomId` 的精确解析，不增加第二次 Tool 调用。

## 2. `search_meeting_rooms`

### 输入

| 参数 | 必填 | 说明 |
|---|---|---|
| `floor` | 是 | 单个楼层，是外部查询粒度 |
| `roomQuery` | 否 | 房间名称、房间号或已知 `roomId` |
| `time` | 否 | 日期和可选的 `start/end`；空对象表示今天完整时间表 |
| `requirements.minCapacity` | 否 | 最小容量 |
| `requirements.equipment` | 否 | 必须全部具备的设备 |
| `requirements.availableOnly` | 否 | 只保留目标时段完整可用的房间，需要准确时段 |

### 固定处理管道

```text
selectMeet(floor)
  → 返回楼层校验
  → 房间名称/roomId过滤
  → 最小容量过滤
  → 设备过滤
  → 时间轴裁剪与可用性计算
  → availableOnly过滤
  → 按查询维度投影最小结果
```

每个过滤阶段没有对应条件时直接透传。增加新条件时新增独立过滤阶段并注册进管道，不改变
Tool 的业务语义和主流程。

输入 Schema 禁止未声明字段；楼层必须大于0，显式空`roomQuery`和空设备名会被拒绝；
`start/end` 必须成对出现并位于 `09:00-18:30`，
`availableOnly` 必须绑定准确时段。返回体省略未使用的查询维度，避免用 `null` 字段和无关
静态/时间信息占用模型上下文。

零结果只额外返回一个精简的 `emptyReason`：

| `emptyReason` | 语义 |
|---|---|
| `floor_has_no_rooms` | 上游没有返回该楼层的有效会议室 |
| `room_not_found_on_floor` | 该楼层没有指定房间名称或`roomId` |
| `no_room_meets_capacity` | 前序条件命中的房间容量不足 |
| `no_room_has_required_equipment` | 前序条件命中的房间缺少指定设备 |
| `no_room_available_in_time` | 前序条件命中的房间在指定时段均不可用 |

Agent 按原因说明，不通过删除房间、楼层、时间或资源约束偷偷扩大查询。

### 查询与过滤的区别

- “808 在 14:00-15:00 是否可用”：保留 808，返回 `isAvailable=false` 和 `conflicts`。
- “找 14:00-15:00 可用的会议室”：传 `availableOnly=true`，过滤不可用房间。
- 省略 `time`：返回 `roomId/roomName/floor/capacity/equipment`，不返回时间轴。
- 同时有静态和时间条件：一次 Tool 调用返回交集，Agent 不自行按 `roomId` 做集合运算。

多楼层由 Agent 对每个楼层并行调用一次，运行时并发硬上限为 5。

## 3. `book_meeting_room`

输入确定的 `roomId`、日期和时段。Tool 重新读取外部状态并检查冲突；空闲时只创建本地
`meetingRoomBookingDraft`，不调用真实预约接口。真实预约由用户在卡片中确认后执行。

## 4. Agent 与确定性代码的边界

Agent 负责：

- 从自然语言提取楼层、房间、日期、时段和资源要求；
- 多楼层调用编排；
- 根据结构化结果进行简洁说明或推荐。

Tool 和领域代码负责：

- 房间身份精确匹配；
- 日期与时间槽过滤；
- 容量、设备和可用性求交；
- 时间区间冲突判断；
- 只返回完成当前查询需要的字段。

会议室编号最后两位是房间序号、其余前缀是楼层，例如 `808` 属于 8 楼、`1101` 属于
11 楼。若用户明确楼层与编号推断冲突，例如“8楼的606”，Agent 先澄清，不调用Tool，
也不静默选择8楼或6楼。

## 5. Skill 与 Tool 的最小边界

- Skill 只描述模型必须遵守的路由、默认值、澄清和人工确认规则，不复制字段计算逻辑。
- 查询 Tool 只读且无外部副作用，负责一次楼层读取后的确定性求交。
- 草稿 Tool 只接受查询结果中的准确 `roomId` 与时段，不重复承担搜索职责。
- 真实预约不注册为模型 Tool，保持在用户确认后的服务端接口中。
