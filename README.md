# 小原 AI 助手 2.0

小原 AI 助手是一个面向中文办公场景的单 Agent 对话应用。项目以稳定的多轮对话
为核心，提供多会话管理、MySQL 全量持久化、滚动摘要、多模型发现与切换，以及
按需加载的模型运行时。模型调用同时保留 Provider 原始响应与 LangChain 转换后的
`AIMessage`，用于本地开发和问题调试。

当前版本始终加载“员工信息查询”“查询/预约会议室”和“查询/提交/撤销请假”Tool。业务 Tool
统一调用本机 `mock-sandbox:18080`，会话、草稿和审计数据统一持久化到 MySQL。

## 项目能力

| 模块 | 当前能力 |
|---|---|
| Agent 运行时 | LangChain `create_agent`，结构化 System Prompt，并为会议室 Skill 注入工作流约束 |
| 员工信息查询工具 | 一个可组合 `find_person` Tool；按全部已传条件过滤并返回人员事实 |
| 会议室 Skill | 调用 Mock Sandbox search/book；支持结构化草稿卡片、人工确认和确认前冲突重查 |
| 请假 Skill | 查询年休假/事假余额，明确确认后提交申请，并可按 requestId 确认撤销 |
| 上下文管理 | 8K/16K/32K/64K 可选预算；固定规则 + 实时时间/卡片状态 + 摘要 + 按轮裁剪的近期原文 + 当前问题 |
| Token 统计 | 每轮汇总 Agent 内全部模型步骤的输入、输出和总 Token，并在回复下方展示 |
| 长对话压缩 | 30/20/10 滚动摘要，后台异步生成，保留全量原文 |
| 会话管理 | 新建、切换、自动命名、重命名、删除、多会话隔离 |
| 消息可靠性 | 用户消息先落库，LLM 后台生成；SSE 事件可回放，离开页面或刷新后继续恢复 |
| 模型管理 | 展示已配置远程 Provider 和可用本地 Provider，支持目录发现与切换 |
| 模型加载 | 按 model ID 首次使用时加载，模型实例与 Agent graph 进程内缓存 |
| 本地模型 | 自动发现本机 Ollama 模型，通过 OpenAI 兼容接口调用 |
| 模型调用调试 | 对照保存 Provider 完整 HTTP 响应和 LangChain `AIMessage` |
| 数据存储 | MySQL 8 + InnoDB，连接池、版本化迁移和全量历史永久保存 |
| Web 服务 | FastAPI JSON API + Vue 3、Vite、TypeScript 单页应用 |

## 仓库与 Git 工作流

- GitHub：<https://github.com/nonoshuaige/XiaoYuanAI>
- 默认远端：`origin`
- 主分支：`main`

```bash
git clone https://github.com/nonoshuaige/XiaoYuanAI.git
cd XiaoYuanAI
git switch main
```

日常开发建议从最新 `main` 创建功能分支，提交前运行本文末尾的后端与前端检查。`.env`、
虚拟环境、前端依赖与构建产物、测试缓存、覆盖率和本地数据库均由 `.gitignore` 排除；
`.env.example` 是唯一应提交的环境变量模板。

## 设计文档

- [`docs/query-capability-matrix.md`](docs/query-capability-matrix.md)：员工信息与会议室查询能力矩阵、输入输出示例和边界。
- [`docs/meeting-room-tool-design.md`](docs/meeting-room-tool-design.md)：会议室查询过滤管道、预约草稿与 Agent/确定性代码边界。
- [`docs/agent-prompt-security.md`](docs/agent-prompt-security.md)：Prompt、消息权限、Tool 数据流和副作用安全边界。
- [`docs/find-person-tool-porting-guide.md`](docs/find-person-tool-porting-guide.md)：`find_person` 接口、Schema、返回协议与移植说明。

## 快速启动

项目使用 Python 3.10+ 和 Node.js 20.19+，推荐在独立虚拟环境中运行。首次启动先
创建本地配置、安装依赖并构建 Vue 前端：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

cd frontend
npm ci
npm run build
cd ..

python -m app.main
```

浏览器访问 <http://localhost:8000>。

上述是日常运行/生产方式：`npm run build` 只需在首次安装或前端源码发生变化后执行；
构建完成后，FastAPI 会直接提供 Vue 静态资源，因此平时只需要启动 `python -m app.main`，
不需要额外常驻 Vite 进程。

### macOS 后台运行

仓库提供 [`launchd/com.xiaoyuanai.app.plist`](launchd/com.xiaoyuanai.app.plist)，用于让
XiaoYuanAI 登录后自动启动并在异常退出后拉起。安装前先按本机实际位置修改 plist 中的
Python 路径、`WorkingDirectory` 和日志路径，然后执行：

```bash
cp launchd/com.xiaoyuanai.app.plist ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.xiaoyuanai.app.plist
launchctl kickstart -k "gui/$(id -u)/com.xiaoyuanai.app"
```

查看状态及重启：

```bash
launchctl print "gui/$(id -u)/com.xiaoyuanai.app"
launchctl kickstart -k "gui/$(id -u)/com.xiaoyuanai.app"
curl http://127.0.0.1:8000/health
```

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
# 单次模型 HTTP 请求超时秒数
MODEL_REQUEST_TIMEOUT_SECONDS=60

# MySQL 8+
XIAOYUAN_MYSQL_HOST=127.0.0.1
XIAOYUAN_MYSQL_PORT=3306
XIAOYUAN_MYSQL_USER=root
XIAOYUAN_MYSQL_PASSWORD=
XIAOYUAN_MYSQL_DATABASE=xiaoyuan_ai
XIAOYUAN_MYSQL_POOL_SIZE=10
XIAOYUAN_MYSQL_CONNECT_TIMEOUT=5

# 本地企业接口测试沙箱
XIAOYUAN_MOCK_SANDBOX_URL=http://127.0.0.1:18080
XIAOYUAN_MOCK_SANDBOX_TIMEOUT=5
# 前端首次打开时使用的默认用户；可在聊天页左下角按工号切换
XIAOYUAN_MOCK_USER_ID=000328
XIAOYUAN_MOCK_USER_NAME=郑子涵
```

`.env` 已被 `.gitignore` 排除，不会提交到 Git。Python OpenAI 客户端使用的
`base_url` 通常需要包含 `/v1` 路径前缀。应用现在把 Mock Sandbox 作为就绪依赖，
启动 XiaoYuanAI 前应确保本地服务可访问：

```bash
curl http://127.0.0.1:18080/api/ready
```

聊天页左下角会显示当前 Mock 用户。更换用户时只需输入工号；后端会通过 person
员工信息服务查询姓名并再次校验，姓名不能由前端编辑，服务中不存在的工号不会生效。

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
MySQL pending 轮次 + 后台 ChatJobManager
        │
        ▼
AgentRuntime
  │
  ├── 按 model ID 加载并缓存模型/graph
  ├── 按 session 串行执行对话轮次
  ├── 从 MySQL 重建模型上下文
  ├── 记录 Provider 响应与 LangChain 转换结果，供调试对照
  ├── 按需调用当前环境实际注册的工具
  └── 提交异步摘要任务
        │
        ├── OpenAI 兼容模型服务
        ├── Mock Sandbox :18080
        │     ├── 人员信息 search
        │     ├── 会议室 selectMeet / create
        │     └── 请假余额 / 申请 / 撤销
        └── MySQL 会话、预约草稿与审计存储
```

## Agent 运行时

主链位于 `app/agent/runtime.py`，System Prompt 及其动态构建逻辑位于
`app/agent/prompts.py`。运行时通过外部企业接口统一注册“员工信息查询、查询/预约会议室、
查询/提交/撤销请假”能力，本地不再维护员工或会议室数据：

```text
收到用户问题
  → 前端预生成 sessionId，用户消息和所选 model ID 落库
  → 轮次标记为 pending，HTTP 立即返回 202
  → 后台 ChatJobManager 领取任务
  → 从 MySQL 读取最新摘要和未覆盖原文
  → 构建本次临时消息 State
  → 流式调用选中的模型，按需执行工具
  → 文本增量和工具生命周期事件写入短期内存缓冲，再通过 SSE 推送
  → 保存 Provider 响应、完整 AIMessage 和 assistant 回复
  → 轮次标记为 completed
  → 前端收到 completed 事件后用完整会话记录校准消息和卡片
  → 必要时提交后台摘要任务
```

用户切换会话、离开聊天页或刷新不会取消后台任务。前端重新进入会话时从 MySQL
恢复 pending 轮次，并重新连接该轮 SSE；同一进程内，服务端可从保留 5 分钟的内存
缓冲按事件 ID 重放尚未展示的状态、文本增量和工具调用进度。SSE 断流时浏览器会自动
重连，并以会话轮询作为兜底。服务重启时会扫描持久化 pending 轮次并重新提交，但
不会恢复重启前的中间 token；已结束轮次直接从最终消息合成 `completed/failed` 事件。
同一会话在上一轮完成前拒绝重复发送，但不阻止用户切换到其他会话。

模型调用失败时：

- 用户原文仍然保留；
- 轮次状态更新为 `failed`；
- 错误信息写入轮次记录；
- Provider 已返回的错误响应仍写入模型调用调试记录；
- 不伪造 assistant 回复。

同一进程内，同一 session 的模型请求通过独立锁串行执行；不同 session 可以并行。

## 员工信息查询 Skill

`people-directory` Skill 提供一个可组合的 `find_person` Tool。内部 finder 共用外部人员
查询接口 `/api/eop-olk/api/v2/addressbook/search` 访问 18080 测试沙箱，并映射字段为：

| 字段 | 返回字段 | 约束 |
|---|---|---|
| 工号 | `employee_id` | 对应 `loginCode` |
| 姓名 | `name` | 可重名 |
| 手机号 | `phone` | 优先取兼容接口可用电话字段 |
| 邮箱 | `email` | 优先取兼容接口可用邮箱字段 |
| 部门 | `department` | 对应 `orgName/orgFullName` |

Tool 支持一次传入工号、完整手机号、手机号尾号、完整姓名或姓名片段等多个条件。每个条件
由对应的底层 `find_by_*` 对远程宽匹配候选执行字段过滤，Tool 再按员工工号取交集，只返回
同时满足全部条件的 `people`。Tool 不判断用户说法是否正确；待核对的说法由 Agent 与返回
人员字段比较。完整协议见
[`docs/find-person-tool-porting-guide.md`](docs/find-person-tool-porting-guide.md)。

本项目不建员工表、不缓存员工信息查询结果，也不提供员工 CRUD 页面。员工数据的唯一事实来源
是外部接口。

### 外部测试沙箱

本地 `mock-sandbox` 是独立服务，默认监听 18080。XiaoYuanAI 只通过 HTTP 访问它，不导入
其数据库、种子数据或页面。启动 XiaoYuanAI：

```bash
/Users/zypro/Desktop/pythonenv/envs/XiaoYuan/bin/python -m uvicorn \
  app.main:app --host 127.0.0.1 --port 8000
```

可用示例问题：

- `帮我找工号 160218`
- `查一下 7 楼明天下午两点可用的会议室`
- `查询我的年休假余额`

项目不再携带 SQLite 数据文件和一次性迁移脚本。MySQL 中仅保留当前 Agent 生成的预约
确认草稿；员工、房间库存和预约事实始终以 18080 返回为准。

Agent 始终注册：

- `people-directory` Skill：提供一个可组合的 `find_person` Tool。
  - Tool 接受工号、完整手机号、手机尾号、完整姓名和姓名片段等可选查询条件；每个已传条件
    都会调用对应的底层 `find_by_*`，最终只返回全部条件交集中的 `people`。
  - 空交集会区分“某个条件自身无匹配”和“各条件指向不同员工”；显式空值或非法手机号
    文本在调用外部接口前拒绝，Agent 不会擅自删除条件扩大查询。
  - Tool 不判断用户说法是否正确。例如“工号 X、手机尾号 Y 的人是不是张三”只用 X 和 Y
    查询，再由 Agent 将返回人员的姓名与“张三”比较。
- `meeting-room-assistant` Skill：查询会议室信息、推荐可用候选并推送待确认预约卡片。
  - `search_meeting_rooms` Tool：一次查询一个楼层，并在服务端按可选的房间名称/`roomId`、
    日期时段、最小容量、设备和空闲状态依次过滤。省略 `time` 时返回静态信息，传入
    `time` 时返回过滤后的时间表、明确的 `isAvailable` 和冲突信息；静态与时间条件并存时
    由同一次 Tool 调用返回交集，不再要求 Agent 自行按 `roomId` 交叉。
  - 零结果会用 `emptyReason` 区分楼层无房间、指定房间不存在、容量不足、设备不符和时段
    无空闲；空房间名、空设备名及非正楼层不会被静默当成“未传”。楼层与房间号冲突时先澄清。
  - `book_meeting_room` Tool：模型传入 `roomId` 和 `time`，人数可选且默认5。服务端重新查询最新状态；
    冲突时返回占用方与占用时间，空闲时只推送 `meetingRoomBookingDraft`，真实预约仍由
    用户在卡片界面确认。
  - 会议室数字编号的最后两位是房间序号，其余前缀是楼层，例如 `808` 属于8楼、
    `1101` 属于11楼。Agent 直接推断，不为判断楼层调用 Tool；该规则不适用于外部
    `roomId`。多楼层查询可并行执行，一轮最多5个楼层。
  - 会议室每天可用时段为 `09:00-18:30`，以30分钟为一个时间槽。
  - 楼层是查询 Tool 唯一必填的业务条件。已知楼层后可以直接调用，也可以由模型结合
    上下文判断是否继续交流。省略 `search_meeting_rooms.time` 表示静态查询，传空对象表示
    今天完整时间表；未给日期默认今天。准确开始和结束时间只在仅看空闲结果或最终创建
    预约卡片时必须提供。
  - 未提供会议时长时默认60分钟，未提供参会人数时默认5人；Agent 不为这两项追问。
    只给开始时间时自动按60分钟推导结束时间；容量筛选时可将静态容量与时间表交叉。
  - Agent 根据查询结果自行选择合适、简短、精炼的说法，让用户看清可用性并自行选择；
    除非用户明确要求推荐，否则不代选房间或时段。
  - Tool 按楼层返回数据，但最终回答按用户 query 裁剪房间和字段：指定房间时只回答该
    房间，只问日程时不附加容量或设备；用户要求比较或备选时才扩展范围。
- `leave-request` Skill：集中管理余额查询、请假申请和撤销确认。
  - `queryLeaveBalance` Tool：获取当前沙箱用户的年休假、事假类型和剩余天数。
  - `applyLeave` Tool：只接受年休假或事假；必须收齐日期、全天/上午/下午和事由，并在
    用户明确确认后提交，返回真实 `requestId`。
  - `cancelLeave` Tool：必须使用真实 `requestId`，并在用户明确确认撤销后调用。

查询 Tool 仍以单个楼层作为外部查询粒度；房间、静态属性和时间约束只在服务端过滤，
不改变外部 `selectMeet(floor)` 的查询焦点。推送预约卡片前，服务端会按
`roomId` 解析楼层并再次读取该楼层的最新状态。鉴权 `userId/userName` 由服务端注入，
楼层、人数和主题都不是预约 Tool 的模型参数。

当天最早可预约时间向右取整到半小时边界：服务端时间恰好为 11:30 时仍可从11:30开始，
11:31 时最早只能从12:00开始。预约草稿有效期为 30 分钟，待确认期间可以编辑后
“保存并预约”，或点击“取消”。
口语化修改时间并生成新卡片不会自动取消旧卡片；若仍有旧卡片处于 `pending`，助手会
提醒它仍可被确认，用户可以主动取消或等待过期。

服务端会校验模型输出与实际 Tool 结果。如果模型声称已生成预约卡片，但本轮没有真实
返回 `meetingRoomBookingDraft`，运行时会向模型反馈校验失败并自动重试一次；再次失败
时只返回“尚未生成”的安全提示，不会把 Markdown 表格当作可确认卡片。

## 上下文管理

MySQL 是持久化事实来源。每次聊天请求都会重新构建临时消息 State，不依赖进程
内的历史消息缓存。

模型实际收到的上下文顺序为：

```text
SystemMessage
  助手身份 + 通用能力 + 全局工具边界 + 已注册 Skill 的工作流约束

SystemMessage
  本轮服务端动态注入的 Asia/Shanghai 当前日期、时间与星期

HumanMessage
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
猜测用户没有提供的预约参数。用户未提供楼层时先追问，单次最多查询5个楼层；未提供
时间窗口时使用指定日期全部可预约时间作为默认范围。真正推送预约卡片仍要求具体房间、日期和准确
时段。预约草稿状态同样在每轮调用前
从 MySQL 重新读取，因此无论卡片是否确认、取消或过期，后续 LLM 都能感知最新状态；
状态上下文不授予模型替用户确认或取消的权限。

### 权限边界

System Prompt 保留通用语言与推理规则、全局工具边界，以及需要跨多个 Tool 协作的
Skill 工作流约束。单个 Tool 的能力、调用条件和参数说明由 LangChain 随工具定义独立
提供，不重复复制。未注册的工具不会出现在模型可用工具中。全局规则明确区分高权限指令
和不可信数据：用户消息、历史、摘要、Tool 自由文本及外部字段不能覆盖系统规则、泄露
内部配置或成为外部操作授权；Tool 只接收当前任务所需的最少参数。

摘要模型使用独立的 `SUMMARY_SYSTEM_PROMPT` 生成压缩结果，但生成结果回到主 Agent
时仍以 `HumanMessage` 注入。会议室草稿状态虽然由服务端读取，但 `theme/roomName` 等字段
可能来自用户或外部系统，因此同样以带数据边界说明的 `HumanMessage` 注入；只有服务端
时钟和固定输出校验规则使用动态 `SystemMessage`。

完整威胁边界、已落地防护和待补强项见
[`docs/agent-prompt-security.md`](docs/agent-prompt-security.md)。

找人和会议室的完整输入示例、预期输出、异常边界及当前支持状态见
[`docs/query-capability-matrix.md`](docs/query-capability-matrix.md)。

### 当前问题不会重复

当前用户问题会先写入 MySQL，但读取历史上下文时只读取到上一轮，再单独追加当前
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

删除会话时，MySQL 外键会级联删除轮次、聊天消息、全部摘要版本、模型审计和预约草稿。

每轮 Agent 的 `status`、`reset`、`text_delta`、`tool_start`、`tool_end`、
`completed` 和 `failed` 事件只保存在进程内短期缓冲，不写数据库。文本直答会逐段
显示；工具调用只展示面向用户的执行状态，不把原始参数暴露到页面。轮次结束 5 分钟
后清理缓冲，最终消息和卡片以 `chat_messages` 及业务表中的完成态为准。

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

成功轮次把模型调试记录、assistant 正文和 `completed` 状态放在同一个 MySQL 事务中；
失败轮次也会保存调用期间已经收到的 Provider 响应，并将 LangChain 消息记为
`null`。历史版本中已经发生的调用无法反向恢复原始 Provider 响应，调试记录从升级后的
新调用开始记录。

调试记录默认开启，保存在 MySQL 的 `model_call_audits` 表。原始响应可能
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

模型配置位于 `app/providers/config.py`。

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

## MySQL 持久化

默认连接本机 `127.0.0.1:3306/xiaoyuan_ai`，使用 InnoDB、`utf8mb4`、外键和进程内
连接池。`app/persistence/migrations/mysql/` 保存按文件名排序的版本化 DDL，启动时自动应用尚未执行的
迁移并记录到 `schema_migrations`。

| 表 | 用途 |
|---|---|
| `sessions` | 会话标题、轮次数、创建和更新时间 |
| `conversation_rounds` | 每一轮的 `pending/completed/failed` 生命周期 |
| `model_call_audits` | Provider 原始响应和完整 `AIMessage`；模型、状态和错误从轮次表关联读取 |
| `chat_messages` | 所有 user/assistant 原文 |
| `conversation_summaries` | 累计摘要正文、覆盖范围和历史版本 |
| `meeting_room_booking_drafts` | 预约草稿参数、所属会话/轮次、状态、有效期和最终预约凭证 |
| `schema_migrations` | 已执行的 MySQL DDL 版本 |

`chat_messages` 使用 `(session_id, round_no, role)` 作为复合主键。摘要只影响模型
上下文的构建方式，不会删除或改写完整聊天记录。

## Vue 前端

前端源码位于 `frontend/`，使用 Vue 3、Vite、TypeScript、Vue Router 和 Pinia。
本地只提供 Agent 对话页面：

| URL | Vue 页面 |
|---|---|
| `/` | 对话、模型和会话管理 |

聊天页面包含：

- 虚拟“新对话”入口；
- 会话列表、轮数和当前会话状态；
- 会话切换、重命名和删除确认弹窗；
- 按 Provider 分组的模型选择器；
- 当前 session 和模型的 `localStorage` 记忆；
- 服务端完整聊天记录恢复；
- Agent 状态、工具调用进度和模型正文的 SSE 实时输出；
- 刷新、切换会话和短时断线后的内存事件重放；
- Markdown 安全渲染；
- 可编辑、可取消、可过期、需要人工确认的会议室预约卡片；

浏览器只保存当前 `sessionId` 和选中的 model ID，消息正文以 MySQL 为唯一来源。

只有开发和调试 Vue 源码、需要热更新时，才需要让 FastAPI 与 Vite 两个进程同时运行。
Vite 会把 `/api` 代理到 FastAPI：

```bash
# 终端一
python -m app.main

# 终端二
cd frontend
npm run dev
```

访问 <http://127.0.0.1:5174>。生产运行前执行 `npm run build`，FastAPI 会从
`frontend/dist` 提供入口和带哈希的静态资源。可通过 `XIAOYUAN_FRONTEND_DIST`
覆盖构建目录。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/models` | 获取当前配置下的模型目录 |
| `GET` | `/api/models?refresh=true` | 强制刷新 Coding Plan 模型目录 |
| `POST` | `/api/chat` | 持久化消息并提交后台模型任务，返回 `202 pending` |
| `POST` | `/api/sessions/{sessionId}/rounds/{round}/retry` | 取消仍在生成的最新轮次，并使用原消息、模型和上下文窗口重试 |
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
  "model": "qwen3d6-27b",
  "contextWindowTokens": 16384
}
```

继续已有会话：

```json
{
  "message": "再精简一点",
  "sessionId": "服务返回的会话 ID",
  "model": "qwen3-coder-plus",
  "contextWindowTokens": 8192
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
  "contextWindowTokens": 16384,
  "modelCallUrl": "/api/sessions/会话 ID/rounds/1/model-call",
  "artifacts": []
}
```

最终回复和 Tool 卡片通过 `GET /api/sessions/{sessionId}` 获取。`artifacts` 只包含
Tool 真实生成的结构化卡片；普通 Markdown 或模型自行拼出的 JSON 不会被当作预约卡片。

会话消息同时返回 `inputTokens`、`outputTokens`、`totalTokens`、
`contextEstimatedTokens`、`contextWindowTokens`、`contextTruncated` 和
`contextDroppedRounds`。Token 用量优先取模型返回的真实 usage；流式 Provider 不返回
usage 时使用本地混合中英文计数并在界面标记 `≈`。上下文规模是发送前的估算值，用于
判断所选预算是否合适。裁剪始终按完整轮次从最旧内容开始，不会
拆散一轮用户/助手消息，也不会裁掉当前问题、实时规则或工具定义。
若一轮内发生 Tool 调用，`inputTokens` 是这一轮所有模型调用输入量的累计值，而
`contextEstimatedTokens/contextWindowTokens` 描述首次模型调用的上下文规模与预算；
因此前者可能高于后者，这不表示某一次请求已经突破上下文上限。

## 项目结构

```text
.
├── app/
│   ├── main.py              # FastAPI 组合根、页面与 JSON API
│   ├── agent/               # Agent 运行时、Prompt、Skill 协议和后台任务
│   ├── features/            # 按业务域组织的 Skill、Tool 与领域逻辑
│   │   ├── people/          # 员工信息查询 Skill 与 Tool
│   │   ├── meeting_room/    # 会议室 Skill、Tool、领域模型、Gateway 和草稿仓储
│   │   └── leave/           # 请假 Skill 与 Tool
│   ├── integrations/
│   │   └── mock_sandbox/    # 18080 HTTP 客户端、配置和统一错误映射
│   ├── persistence/         # MySQL 连接池、会话仓储和版本化迁移
│   │   └── migrations/mysql/
│   └── providers/           # 模型 Provider 配置、发现与调用审计
├── frontend/
│   ├── src/api/             # 类型化 FastAPI 客户端
│   ├── src/components/      # 品牌、弹窗、状态和预约卡片组件
│   ├── src/router/          # Agent 对话页客户端路由
│   ├── src/stores/          # Pinia 聊天和会话状态
│   ├── src/styles/          # 统一设计系统与响应式样式
│   ├── src/views/           # Agent 对话页面
│   ├── package.json         # 前端依赖、构建和测试脚本
│   └── vite.config.ts       # Vite 构建及开发代理
├── tests/
│   ├── agent/               # 会话、上下文、失败恢复、摘要和任务调度测试
│   ├── api/                 # FastAPI 聊天接口测试
│   ├── features/            # 员工信息查询、会议室等业务域测试
│   ├── integrations/        # 18080 字段映射、请求体和人工确认边界测试
│   └── providers/           # Provider 配置、模型发现和调用审计测试
├── docs/                    # Tool 设计、能力矩阵、安全边界和移植说明
├── .gitignore               # 本地配置、依赖、构建和运行产物排除规则
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
- MySQL 重启恢复全量记录；
- 用户消息先落库及失败轮次保留；
- SSE 文本增量、工具生命周期、完成事件及短期内存重放；
- Provider 完整 HTTP 响应与 LangChain `AIMessage` 持久化；
- 摘要保持用户消息权限，不进入 System Prompt；
- 第一次和后续累计滚动摘要；
- 摘要任务阻塞时继续聊天不丢上下文；
- 模型切换及未知模型拒绝；
- Provider 未配置时不出现在目录；
- 模型实例和 Agent graph 按需加载并复用。
- 上下文预算校验、按完整轮次裁剪及多步骤模型 Token usage 汇总。
- Ollama 模型自动发现、Provider 隔离和本地 OpenAI 兼容地址。
- Vue API 错误归一化、日期边界和生产构建。
- Mock Sandbox 员工信息查询、会议室和请假协议映射，以及会议室确认前零远端写入。

## 当前边界

当前版本仍有以下明确边界：

- 业务 Tool 当前接入的是本机 Mock Sandbox，尚未接企业生产认证、签名和真实网关；
- 会议室已具备前端人工确认卡片；请假提交/撤销目前依赖对话内的显式确认约束，尚无独立
  前端审批卡片；
- 30/20/10 仍负责异步摘要；首次模型输入另受用户选择的估算 Token 预算限制；
- 摘要尚未记录 prompt 版本、模型、source hash 和 token 用量；
- session 串行锁只在单个 Python 进程内有效；
- 默认面向本地使用，尚未实现用户认证、租户隔离和限流；
- 后台任务当前使用进程内线程池；多实例生产部署仍需独立任务队列和跨进程领取租约。

这些能力应在进入多人或多进程生产部署前补齐。

## 版本

当前版本：**2.4 企业 Tool 契约与实时恢复**
