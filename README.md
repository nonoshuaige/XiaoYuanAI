# 小原 AI 助手 2.0

小原 AI 助手是一个面向中文办公场景的单 Agent 对话应用。项目以稳定的多轮对话
为核心，提供多会话管理、SQLite 全量持久化、滚动摘要、多模型发现与切换，以及
按需加载的模型运行时。模型调用同时保留 Provider 原始响应与 LangChain 转换后的
`AIMessage`，用于本地开发和问题调试。

当前版本始终加载“找人”“查询会议室”和“预约会议室”Tool；通过沙箱启动时额外
提供可直接验证数据库写入结果的员工与会议室操作页面。

## 项目能力

| 模块 | 当前能力 |
|---|---|
| Agent 运行时 | LangChain `create_agent`，结构化 System Prompt，并为会议室 Skill 注入工作流约束 |
| 找人工具 | 工号 > 手机号 > 姓名主查询，全部线索一致性校验，重名候选消歧 |
| 会议室 Skill | 支持当前半小时即时预约、结构化草稿卡片、人工确认/取消和确认前冲突重查 |
| 会议室日程沙箱 | Vue 按日期、楼层和房间展示 09:00–18:00 半小时日程 |
| 上下文管理 | 固定系统规则 + 实时时间/预约卡片状态 + 用户层历史摘要 + 未覆盖原文 + 当前问题 |
| 快捷回答 | 候选收敛为 2–4 个明确选项时，展示当前轮可直接发送的临时回答按钮 |
| 长对话压缩 | 30/20/10 滚动摘要，后台异步生成，保留全量原文 |
| 会话管理 | 新建、切换、自动命名、重命名、删除、多会话隔离 |
| 消息可靠性 | 用户消息先落库，LLM 后台生成；SSE 事件可回放，离开页面或刷新后继续恢复 |
| 模型管理 | 展示已配置远程 Provider 和可用本地 Provider，支持目录发现与切换 |
| 模型加载 | 按 model ID 首次使用时加载，模型实例与 Agent graph 进程内缓存 |
| 本地模型 | 自动发现本机 Ollama 模型，通过 OpenAI 兼容接口调用 |
| 模型调用调试 | 对照保存 Provider 完整 HTTP 响应和 LangChain `AIMessage` |
| 数据存储 | SQLite + WAL，聊天记录、轮次、模型调试记录、摘要版本永久保存 |
| Web 服务 | FastAPI JSON API + Vue 3、Vite、TypeScript 单页应用 |

## 仓库定位

- GitHub：<https://github.com/nonoshuaige/XiaoYuanAI>
- 本地目录：`/Users/zypro/Desktop/XiaoYuanAI/XiaoYuanAI`
- Git 远端：`origin` → `https://github.com/nonoshuaige/XiaoYuanAI.git`
- GitHub 推送账号：`nonoshuaige`
- 主分支：`main`

后续开发、提交和推送均以这个本地目录及其 `origin` 远端为准。

## 快速启动

项目使用 Python 3.10+ 和 Node.js 20.19+，推荐在独立虚拟环境中运行。首次启动先
安装并构建 Vue 前端：

```bash
conda activate agent-env
pip install -r requirements.txt

cd frontend
npm install
npm run build
cd ..

python server.py
```

浏览器访问 <http://localhost:8000>。

上述是日常运行/生产方式：`npm run build` 只需在首次安装或前端源码发生变化后执行；
构建完成后，FastAPI 会直接提供 Vue 静态资源，因此平时只需要启动 `python server.py`，
不需要额外常驻 Vite 进程。

### 模型配置

复制 `.env.example` 为 `.env`，然后只填写需要使用的远程 Provider。没有配置
API Key 的远程 Provider 不会出现在模型列表中；本地 Ollama 可自动发现。

```dotenv
# Coding Plan
DASHSCOPE_API_KEY=你的 API Key
MODEL_NAME=qwen3-coder-plus
OPENAI_API_BASE=https://coding.dashscope.aliyuncs.com/v1/

# Qwen3D，可选
QWEN3D6_API_KEY=你的 API Key
QWEN3D6_MODEL_NAME=qwen3d6-27b
QWEN3D6_API_BASE=你的 OpenAI 兼容 API 地址

# Ollama 本地模型，可选且不需要 API Key
OLLAMA_API_BASE=http://127.0.0.1:11434/v1/

# 可选；未配置或不可用时回退到第一个可调用模型
DEFAULT_MODEL_ID=qwen3-coder-plus
```

`.env` 已被 `.gitignore` 排除，不会提交到 Git。Python OpenAI 客户端使用的
`base_url` 通常需要包含 `/v1` 路径前缀。

## 总体架构

```text
浏览器中的 Vue SPA
  │
  ▼
Vue Router + Pinia + 类型化 API 客户端
  │
  ▼
FastAPI API 与前端构建产物
  │
  ├── 模型目录与 Provider 配置
  ├── 会话增删改查
  ├── 聊天任务受理（202 Accepted）
  └── 可重连的 Agent SSE 事件流
        │
        ▼
SQLite pending 轮次 + 后台 ChatJobManager
        │
        ▼
AgentRuntime
  │
  ├── 按 model ID 加载并缓存模型/graph
  ├── 按 session 串行执行对话轮次
  ├── 从 SQLite 重建模型上下文
  ├── 记录 Provider 响应与 LangChain 转换结果，供调试对照
  ├── 按需调用当前环境实际注册的工具
  └── 提交异步摘要任务
        │
        ├── OpenAI 兼容模型服务
        └── SQLite 会话、通讯录与沙箱会议室存储
```

## Agent 运行时

主链位于 `agent.py`，System Prompt 及其动态构建逻辑位于 `prompts.py`。普通环境
与沙箱环境注册相同的“找人、查询会议室、预约会议室”工具列表：

```text
收到用户问题
  → 前端预生成 sessionId，用户消息和所选 model ID 落库
  → 轮次标记为 pending，HTTP 立即返回 202
  → 后台 ChatJobManager 领取任务
  → 从 SQLite 读取最新摘要和未覆盖原文
  → 构建本次临时消息 State
  → 流式调用选中的模型，按需执行工具
  → 文本增量和工具生命周期事件先写 SQLite，再通过 SSE 推送
  → 保存 Provider 响应、完整 AIMessage 和 assistant 回复
  → 轮次标记为 completed
  → 前端收到 completed 事件后用完整会话记录校准卡片和快捷回答
  → 必要时提交后台摘要任务
```

用户切换会话、离开聊天页或刷新不会取消后台任务。前端重新进入会话时从 SQLite
恢复 pending 轮次，并重新连接该轮 SSE；服务端按事件 ID 重放尚未展示的状态、文本
增量和工具调用进度。SSE 断流时浏览器会自动重连，并以会话轮询作为兜底。服务重启
时会扫描持久化 pending 轮次并重新提交。
同一会话在上一轮完成前拒绝重复发送，但不阻止用户切换到其他会话。

模型调用失败时：

- 用户原文仍然保留；
- 轮次状态更新为 `failed`；
- 错误信息写入轮次记录；
- Provider 已返回的错误响应仍写入模型调用调试记录；
- 不伪造 assistant 回复。

同一进程内，同一 session 的模型请求通过独立锁串行执行；不同 session 可以并行。

## 找人工具

工具 API 名为 `find_person`，中文能力名为“找人”。员工目录保存在现有 SQLite
数据库的 `people` 表中：

| 字段 | SQLite 列 | 约束 |
|---|---|---|
| 工号 | `employee_id` | 主键 |
| 姓名 | `name` | 必填，可重名 |
| 手机号 | `phone` | 必填、唯一 |
| 部门 | `department` | 必填 |

用户至少需要明确提供工号、手机号或姓名中的一项。若同时提供多项，工具按
`工号 > 手机号 > 姓名` 选择主查询字段，再用其余身份线索和部门校验结果；线索不一致
时返回冲突状态，不返回某位员工。工号、手机号、姓名均为精确匹配。姓名查询得到多人时，
工具返回候选而不是擅自选择。

生产 Agent 首次初始化时会自动建表，但不会写入虚构员工。同步员工数据时可调用
`PeopleStore.upsert(...)`；之后 Agent 即可查询同一数据库中的记录。

### 虚构业务沙箱

沙箱模式与普通模式共用 `data/xiaoyuan.db`。它只控制聊天页左上角是否显示
“员工沙箱”和“会议室沙箱”入口，以及对应页面/API 是否开放，不再切换整套数据库。
员工、会议室、预约、会话、摘要和模型审计记录全部保存在同一个数据库中。

正常模式启动在 8000 端口，聊天页不会显示业务沙箱入口：

```bash
/Users/zypro/Desktop/pythonenv/envs/XiaoYuan/bin/python -m uvicorn \
  server:app --host 127.0.0.1 --port 8000
```

需要同时查看沙箱时，可另开终端在 8001 端口启动沙箱模式。它会显示员工和会议室
沙箱入口；如果统一数据库是首次创建，还会幂等写入 10 条虚构员工记录，并初始化
6F/7F/8F 共 9 间会议室和当日示例日程：

```bash
/Users/zypro/Desktop/pythonenv/envs/XiaoYuan/bin/python sandbox.py --port 8001
```

此时正常模式访问 <http://127.0.0.1:8000>，沙箱模式访问
<http://127.0.0.1:8001>，两者共同读写 `data/xiaoyuan.db`。只初始化和验证数据、
不启动服务：

```bash
/Users/zypro/Desktop/pythonenv/envs/XiaoYuan/bin/python sandbox.py --seed-only
```

可用示例问题：

- `帮我找工号 XY-S003`
- `手机号 13800000004 是谁？`
- `帮我找研发部的陈晨`
- `帮我找张三`（会返回研发部和财务部两位候选）
- `找工号 XY-S003、手机号 13800000001、姓名张三`（会提示线索冲突）

沙箱仍会使用 `.env` 中配置的模型服务。`XIAOYUAN_DB_PATH` 若已配置，会同时作用于
普通模式和沙箱模式；`sandbox.py` 不会再覆盖它。升级后首次启动沙箱模式时，程序会
将旧 `data/sandbox.db` 中的业务数据，以及旧 `data/meeting-room-demo.db` 中的
会议室和预约，一次性安全合并到默认的 `data/xiaoyuan.db`。校验无冲突并记录迁移
状态后，两个旧库都不再参与运行。

启动后可从聊天页侧栏进入“员工沙箱”，也可以直接访问
<http://127.0.0.1:8001/employee-sandbox>。页面支持查看、搜索、新增、编辑和删除，
每次操作都会立即写入统一 SQLite 数据库的 `people` 表。仅在统一数据库文件首次创建
时写入初始虚构数据，因此页面修改和删除（包括删除全部员工）在服务重启后仍会保留。

会议室页面可直接访问 <http://127.0.0.1:8001/meeting-room-sandbox>。页面是只读
日程沙箱：顶部选择日期，下方按楼层展示全部会议室；点击房间可查看 09:00–18:00、
每半小时一段的真实预约状态。页面不创建预约，Agent 的预约结果仍以服务端 Tool
生成的结构化预约草稿卡片为准。

员工沙箱 API：

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/sandbox/status` | 检查当前服务是否为沙箱模式 |
| `GET` | `/api/sandbox/people` | 获取员工列表，支持 `search` 查询 |
| `POST` | `/api/sandbox/people` | 新增员工 |
| `PUT` | `/api/sandbox/people/{employeeId}` | 更新员工全部字段 |
| `DELETE` | `/api/sandbox/people/{employeeId}` | 删除员工 |
| `GET` | `/api/sandbox/meeting-rooms` | 查询会议室与指定日期日程 |
| `POST` | `/api/sandbox/meeting-room-bookings` | 校验冲突并创建预约 |

上述页面与 API 只在通过 `sandbox.py` 启动时开放；普通生产启动返回 404。

Agent 始终注册：

- `find_person` Tool：按工号、手机号或姓名查询员工。
- `meeting-room-booking` Skill：集中管理会议室参数收集、相对日期解析、查询、
  候选选择、明确确认和预约结果校验。
  - `queryMeetingRooms` Tool：楼层、房间、日期或时间任一线索都可发起查询；返回
    09:00–18:00 半小时日程、可用时段以及与所问时长相同的候选时间。
  - `bookMeetingRoom` Tool：参数齐全时生成 `meetingRoomBookingDraft` 结构化草稿，
    模型不得用 Markdown 表格或普通文本模拟卡片。只有用户点击卡片“保存并预约”后，
    服务端才会重新查询冲突并创建真实预约。用户未提供主题时由服务端使用可信预约人
    生成“XXX预约的会议”，模型不猜姓名。

当天预约允许从当前所在的半小时槽即时生效。例如服务端时间为 15:32 时，可以预约
15:30 开始、尚未结束且没有冲突的时段；15:00 等当前半小时槽之前的开始时间仍会被
拒绝。预约草稿有效期为 30 分钟，待确认期间可以编辑后“保存并预约”，或点击“取消”。
口语化修改时间并生成新卡片不会自动取消旧卡片；若仍有旧卡片处于 `pending`，助手会
提醒它仍可被确认，用户可以主动取消或等待过期。

服务端会校验模型输出与实际 Tool 结果。如果模型声称已生成预约卡片，但本轮没有真实
返回 `meetingRoomBookingDraft`，运行时会向模型反馈校验失败并自动重试一次；再次失败
时只返回“尚未生成”的安全提示，不会把 Markdown 表格当作可确认卡片。

## 上下文管理

SQLite 是持久化事实来源。每次聊天请求都会重新构建临时消息 State，不依赖进程
内的历史消息缓存。

模型实际收到的上下文顺序为：

```text
SystemMessage
  助手身份 + 通用能力 + 全局工具边界 + 已注册 Skill 的工作流约束

SystemMessage
  本轮服务端动态注入的 Asia/Shanghai 当前日期、时间与星期

SystemMessage
  本会话全部预约草稿的实时状态（pending/confirmed/cancelled/expired）

HumanMessage（可选）
  历史对话摘要，明确标记为“用户层上下文”

HumanMessage / AIMessage
  摘要范围之后的完整原始对话

HumanMessage
  当前用户问题
```

当前时间上下文在每轮模型调用前重新生成，不写入聊天历史。它用于把用户明确说出的
“今天”“明天”“后天”等相对日期换算为会议室接口需要的 `yyyy/MM/dd`；不会用来
猜测用户没有提供的预约参数。用户只说时间时可跨楼层查询，只说房间时可查看该房间
日程；真正预约仍要求具体房间、日期、时段和明确确认。预约草稿状态同样在每轮调用前
从 SQLite 重新读取，因此无论卡片是否确认、取消或过期，后续 LLM 都能感知最新状态；
状态上下文不授予模型替用户确认或取消的权限。

### 权限边界

System Prompt 保留通用语言与推理规则、全局工具边界，以及需要跨多个 Tool 协作的
Skill 工作流约束。单个 Tool 的能力、调用条件和参数说明由 LangChain 随工具定义独立
提供，不重复复制。未注册的工具不会出现在模型可用工具中。历史摘要不会拼接进
System Prompt，也不能覆盖系统规则或成为外部操作授权。

摘要模型使用独立的 `SUMMARY_SYSTEM_PROMPT` 生成压缩结果，但生成结果回到主 Agent
时仍以 `HumanMessage` 注入。

### 当前问题不会重复

当前用户问题会先写入 SQLite，但读取历史上下文时只读取到上一轮，再单独追加当前
问题。因此同一问题在模型输入中只出现一次。

## 会话与轮次

浏览器点击“新对话”时只展示空白页面，不立即创建数据库记录。用户发送第一条消息
后，服务才生成 `sessionId` 并创建真实会话。

第一条消息的首句会自动成为标题，最长 24 个字符；手动重命名后不会再次被自动标题
覆盖。

一次轮次由用户消息和可选的助手回复组成：

| 状态 | 含义 |
|---|---|
| `pending` | 用户消息已保存，模型调用尚未结束 |
| `completed` | 用户消息和助手回复均已保存 |
| `failed` | 用户消息已保存，但模型没有生成可用回复 |

删除会话时，SQLite 外键会级联删除轮次、聊天消息和全部摘要版本。

每轮 Agent 事件保存在 `chat_events` 表，事件类型包括 `status`、`reset`、
`text_delta`、`tool_start`、`tool_end`、`completed` 和 `failed`。文本直答会逐段
显示；工具调用只展示面向用户的执行状态，不把原始参数暴露到页面。最终消息、卡片和
快捷回答仍以 `chat_messages` 及业务表中的完成态为准。

## 模型调用调试记录

这部分数据用于开发调试，不是额外发送给模型的上下文。它主要帮助定位：

- Provider 实际返回了哪些字段；
- LangChain 转换 `AIMessage` 后保留或丢失了哪些字段；
- token usage、finish reason、response ID 和 request ID 是否正常；
- 本地或远程模型是否返回 reasoning、tool calls 或自定义扩展字段；
- Provider 报错、SDK 重试和最终页面回复之间的对应关系。

每次聊天模型调用都以 `session_id + round_no` 绑定两层原始记录：

1. **Provider HTTP 响应**：Provider ID、请求方法和 URL、状态码、reason phrase、
   全部响应头（保留重复项）、Provider request ID、响应对象 ID、原始解码响应体和
   JSON 解析结果。SDK 重试产生的多个 HTTP 响应会按发生顺序全部保留。Provider
   使用 `text/event-stream` 时不会在响应钩子中提前缓冲正文，以免阻断实时 token；
   此时正文级审计以最终完整 `AIMessage` 为准。
2. **LangChain 转换结果**：完整 `AIMessage.model_dump(mode="json")`，包括
   `content`、`additional_kwargs`、`response_metadata`、`usage_metadata`、
   `id`、tool calls 和 Provider 放入消息中的 reasoning 扩展字段。

成功轮次把模型调试记录、assistant 正文和 `completed` 状态放在同一个 SQLite 事务中；
失败轮次也会保存调用期间已经收到的 Provider 响应，并将 LangChain 消息记为
`null`。历史版本中已经发生的调用无法反向恢复原始 Provider 响应，调试记录从升级后的
新调用开始记录。

调试记录默认开启，保存在本机 `data/xiaoyuan.db`，不会提交到 Git。原始响应可能
包含模型 reasoning 和 Provider 基础设施信息，应按调试数据管理，不要直接公开。
删除会话时，对应的模型调用调试记录会一并级联删除。

查看某轮完整调试信息：

```bash
curl http://127.0.0.1:8000/api/sessions/<sessionId>/rounds/<round>/model-call
```

返回对象中的 `providerResponses` 是 Provider 原始层，
`langchainAIMessage` 是 LangChain 转换层；页面最终只展示
`langchainAIMessage.content` 提取出的正文。

## 滚动摘要

项目采用 30/20/10 策略：

- 未被摘要覆盖的记录达到 30 轮时触发压缩；
- 每次把最早的 20 轮合并进累计摘要；
- 最近 10 轮继续以原文形式保留在活跃上下文。

```text
第 1–30 轮
  摘要：第 1–20 轮
  原文：第 21–30 轮

第 1–50 轮
  摘要：第 1–40 轮
  原文：第 41–50 轮
```

摘要在后台线程执行，不阻塞新的聊天请求。摘要尚未完成时，Agent 会继续读取旧摘要
之后的全部原文，所以压缩变慢只会增加临时上下文长度，不会隐藏尚未覆盖的消息。

每个 session 同时最多运行一个摘要任务。摘要使用期望的上一版 `end_round` 提交，
避免并发任务覆盖更新版本。

## 模型目录与切换

模型配置位于 `config.py`。

### Provider 展示规则

- 只有配置了 API Key 的 Provider 才会出现在 `/api/models`；
- Coding Plan 通过 OpenAI 兼容 `/models` 接口发现模型；
- Qwen3D 使用环境变量中明确配置的模型；
- Ollama 连接本机 `/v1/models`，自动发现已经下载的模型；
- Coding Plan 目录缓存 5 分钟；
- Ollama 目录缓存 30 秒，服务未启动或没有本地模型时不会展示；
- Coding Plan 目录请求失败时优先使用最近一次成功结果，否则使用内置兜底目录。

`GET /api/models` 中每个模型包含：

| 字段 | 含义 |
|---|---|
| `id` | 聊天请求使用的 model ID |
| `providerId` | Provider 稳定标识 |
| `provider` | Provider 展示名称 |
| `default` | 是否为当前解析后的默认模型 |
| `discovered` | 是否曾从 Provider 模型目录实际发现 |
| `callable` | 当前配置是否允许选择并尝试调用 |
| `source` | `live`、`cached`、`configured` 或 `fallback` |

`fallback` 表示服务允许尝试调用内置目录中的模型，不代表 Provider 已实时确认模型
在线。真实可用性仍以聊天请求的 Provider 响应为准。

### 懒加载和缓存

服务启动时不会批量创建所有模型。用户第一次选择某个 model ID 时才会：

1. 校验模型属于当前可调用目录；
2. 创建对应的 `ChatOpenAI` 实例；
3. 创建对应的 LangChain Agent graph；
4. 将模型实例和 graph 缓存在当前进程。

后续再次选择相同 model ID 时直接复用缓存。切换模型不会创建新会话，也不会清空
当前上下文。

### Ollama 本地模型

Ollama 本地 API 默认监听 `http://127.0.0.1:11434`，本地访问不需要 API Key。
项目通过 Ollama 的 OpenAI 兼容 `/v1/chat/completions` 调用模型，并通过
`/v1/models` 发现已经下载的模型。

```bash
ollama list
ollama pull qwen3.5:9b
```

本地模型在 API 中使用 `ollama::<模型名>` 作为选择 ID，避免与远程 Provider 的
同名模型冲突；实际发送给 Ollama 的仍是原始模型名。

## SQLite 持久化

数据库默认位于 `data/xiaoyuan.db`，启用外键和 WAL 模式。

| 表 | 用途 |
|---|---|
| `sessions` | 会话标题、轮次数、创建和更新时间 |
| `conversation_rounds` | 每一轮的 `pending/completed/failed` 生命周期 |
| `model_call_audits` | 调试用 model ID、Provider 原始响应、完整 `AIMessage` 和调用状态 |
| `chat_messages` | 所有 user/assistant 原文，以及助手消息的快捷回答选项 |
| `conversation_summaries` | 累计摘要正文、覆盖范围和历史版本 |
| `people` | 员工目录（工号、姓名、手机号、部门） |
| `meeting_rooms` | 会议室 ID、名称、楼层、容量和设备 |
| `meeting_room_bookings` | 预约日期、时段、主题、预约来源和创建时间 |
| `meeting_room_booking_drafts` | 预约草稿参数、所属会话/轮次、状态、有效期和最终预约凭证 |
| `app_metadata` | 一次性数据库迁移状态（需要时自动创建） |

`chat_messages` 使用 `(session_id, round_no, role)` 作为复合主键。摘要只影响模型
上下文的构建方式，不会删除或改写完整聊天记录。

## Vue 前端

前端源码位于 `frontend/`，使用 Vue 3、Vite、TypeScript、Vue Router 和 Pinia。
三个原有页面被统一为一个 SPA，并保持原 URL：

| URL | Vue 页面 |
|---|---|
| `/` | 对话、模型和会话管理 |
| `/employee-sandbox` | 员工目录 CRUD |
| `/meeting-room-sandbox` | 会议室只读日程 |

聊天页面包含：

- 虚拟“新对话”入口；
- 会话列表、轮数和当前会话状态；
- 会话切换、重命名和删除确认弹窗；
- 按 Provider 分组的模型选择器；
- 当前 session 和模型的 `localStorage` 记忆；
- 服务端完整聊天记录恢复；
- Agent 状态、工具调用进度和模型正文的 SSE 实时输出；
- 刷新、切换会话和断线后的持久化事件重放；
- Markdown 安全渲染；
- 可编辑、可取消、可过期、需要人工确认的会议室预约卡片；
- 候选收敛后的快捷回答按钮，点击后直接作为下一条用户消息发送。

浏览器只保存当前 `sessionId` 和选中的 model ID，消息正文以 SQLite 为唯一来源。

只有开发和调试 Vue 源码、需要热更新时，才需要让 FastAPI 与 Vite 两个进程同时运行。
Vite 会把 `/api` 代理到 FastAPI：

```bash
# 终端一
python server.py

# 终端二
cd frontend
npm run dev
```

访问 <http://127.0.0.1:5173>。生产运行前执行 `npm run build`，FastAPI 会从
`frontend/dist` 提供入口和带哈希的静态资源。可通过 `XIAOYUAN_FRONTEND_DIST`
覆盖构建目录。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/models` | 获取当前配置下的模型目录 |
| `GET` | `/api/models?refresh=true` | 强制刷新 Coding Plan 模型目录 |
| `POST` | `/api/chat` | 持久化消息并提交后台模型任务，返回 `202 pending` |
| `GET` | `/api/sessions` | 获取真实会话列表 |
| `GET` | `/api/sessions/{sessionId}` | 获取会话消息、摘要和压缩状态 |
| `GET` | `/api/sessions/{sessionId}/rounds/{round}/events` | 回放并持续推送该轮 Agent SSE 事件 |
| `GET` | `/api/sessions/{sessionId}/model-calls` | 获取会话全部模型调用调试记录 |
| `GET` | `/api/sessions/{sessionId}/rounds/{round}/model-call` | 获取指定轮完整模型调用调试记录 |
| `PATCH` | `/api/sessions/{sessionId}` | 重命名会话 |
| `DELETE` | `/api/sessions/{sessionId}` | 删除会话及关联数据 |
| `GET` | `/api/meeting-room-booking-drafts/{draftId}` | 获取预约草稿最新状态 |
| `PUT` | `/api/meeting-room-booking-drafts/{draftId}` | 修改并重新校验预约草稿 |
| `GET` | `/api/meeting-room-booking-drafts/{draftId}/room-options` | 按草稿条件刷新会议室选项 |
| `POST` | `/api/meeting-room-booking-drafts/{draftId}/confirm` | 人工确认并创建真实预约 |
| `POST` | `/api/meeting-room-booking-drafts/{draftId}/cancel` | 取消待确认预约草稿 |

首次发送：

```json
{
  "message": "帮我整理今天的会议纪要",
  "model": "qwen3d6-27b"
}
```

继续已有会话：

```json
{
  "message": "再精简一点",
  "sessionId": "服务返回的会话 ID",
  "model": "qwen3-coder-plus"
}
```

任务受理响应包含：

```json
{
  "reply": "",
  "sessionId": "会话 ID",
  "round": 1,
  "status": "pending",
  "title": "自动或手动会话标题",
  "model": "实际使用的 model ID",
  "modelCallUrl": "/api/sessions/会话 ID/rounds/1/model-call",
  "artifacts": [],
  "quickReplies": []
}
```

最终回复、Tool 卡片和快捷回答通过 `GET /api/sessions/{sessionId}` 获取。`artifacts`
只包含 Tool 真实生成的结构化卡片；普通 Markdown 或模型自行拼出的 JSON 不会被
当作预约卡片。`quickReplies` 为 2–4 个可以直接作为用户回答发送的临时短选项；
用户点击或发送新消息后不再显示历史选项。

## 项目结构

```text
.
├── agent.py                 # Agent 运行时、上下文重建、模型 graph 缓存、异步摘要
├── chat_jobs.py             # 持久化 pending 轮次的后台 LLM 任务调度
├── config.py                # Provider 配置、模型发现、目录状态和模型创建
├── conversation_store.py    # SQLite schema、会话、轮次、消息和摘要持久化
├── model_audit.py           # Provider HTTP 捕获与 AIMessage 完整序列化
├── meeting_room_tool.py     # 会议室 SQLite、客户端适配器和两个 Agent Tool
├── server.py                # FastAPI 页面与 JSON API
├── sandbox.py               # 统一数据库迁移、虚构业务数据与沙箱入口
├── frontend/
│   ├── src/api/             # 类型化 FastAPI 客户端
│   ├── src/components/      # 品牌、弹窗、状态和预约卡片组件
│   ├── src/router/          # 三个页面的客户端路由
│   ├── src/stores/          # Pinia 聊天和会话状态
│   ├── src/styles/          # 统一设计系统与响应式样式
│   ├── src/views/           # 聊天、员工和会议室页面
│   ├── package.json         # 前端依赖、构建和测试脚本
│   └── vite.config.ts       # Vite 构建及开发代理
├── tests/
│   ├── test_agent.py        # 会话、上下文、失败恢复、摘要和懒加载测试
│   ├── test_people_tool.py  # 找人主查询、线索冲突、消歧和 Tool 输出测试
│   ├── test_employee_sandbox_api.py # 员工沙箱页面与 CRUD API 测试
│   ├── test_meeting_room_tool.py # 会议室查询、确认、重查与冲突测试
│   ├── test_meeting_room_sandbox_api.py # 会议室只读页面和沙箱 API 测试
│   ├── test_sandbox.py      # 虚构数据、旧库迁移与统一数据库测试
│   ├── test_config.py       # Provider、模型目录和模型选择测试
│   └── test_model_audit.py  # Provider 原始响应与 AIMessage 序列化测试
├── .env.example             # Provider 配置示例
├── requirements.txt         # Python 依赖
└── README.md
```

## 测试

```bash
# 后端与业务回归
python -m pytest -q

# 前端类型、代码规范、单测和生产构建
cd frontend
npm run type-check
npm run lint
npm run test
npm run build
```

当前测试覆盖：

- 首次发送后才创建会话；
- 会话隔离、重命名和删除；
- SQLite 重启恢复全量记录；
- 用户消息先落库及失败轮次保留；
- SSE 文本增量、工具生命周期、完成事件及持久化重放；
- Provider 完整 HTTP 响应与 LangChain `AIMessage` 持久化；
- 摘要保持用户消息权限，不进入 System Prompt；
- 第一次和后续累计滚动摘要；
- 摘要任务阻塞时继续聊天不丢上下文；
- 模型切换及未知模型拒绝；
- Provider 未配置时不出现在目录；
- 模型实例和 Agent graph 按需加载并复用。
- Ollama 模型自动发现、Provider 隔离和本地 OpenAI 兼容地址。
- Vue API 错误归一化、日期边界和生产构建。

## 当前边界

当前版本仍有以下明确边界：

- 会议室 Tool 当前使用本地存储适配器，尚未接企业真实接口和认证；
- 30/20/10 是轮次策略，尚未实现 Token 软阈值和硬上限；
- 摘要尚未记录 prompt 版本、模型、source hash 和 token 用量；
- session 串行锁只在单个 Python 进程内有效；
- 默认面向本地使用，尚未实现用户认证、租户隔离和限流；
- 后台任务当前使用进程内线程池；多实例生产部署仍需独立任务队列和跨进程领取租约。

这些能力应在进入多人或多进程生产部署前补齐。

## 版本

当前版本：**2.1 持久化后台 Agent 与可回放 SSE**
