# 小原 AI 助手 1.3

小原 AI 助手是一个面向中文办公场景的单 Agent 对话应用。项目以稳定的多轮对话
为核心，提供多会话管理、SQLite 全量持久化、滚动摘要、多模型发现与切换，以及
按需加载的模型运行时。模型调用同时保留 Provider 原始响应与 LangChain 转换后的
`AIMessage`，用于本地开发和问题调试。

当前版本已加载首个业务 Tool“找人”，可按自然语言中的工号、手机号或姓名查询内部
员工通讯录。

## 项目能力

| 模块 | 当前能力 |
|---|---|
| Agent 运行时 | LangChain `create_agent`，固定 System Prompt，支持“找人”工具 |
| 找人工具 | 工号 > 手机号 > 姓名优先级查询，部门辅助过滤，重名候选消歧 |
| 上下文管理 | 固定系统规则 + 用户层历史摘要 + 未覆盖原文 + 当前问题 |
| 长对话压缩 | 30/20/10 滚动摘要，后台异步生成，保留全量原文 |
| 会话管理 | 新建、切换、自动命名、重命名、删除、多会话隔离 |
| 消息可靠性 | 用户消息先落库，轮次具有 `pending/completed/failed` 状态 |
| 模型管理 | 展示已配置远程 Provider 和可用本地 Provider，支持目录发现与切换 |
| 模型加载 | 按 model ID 首次使用时加载，模型实例与 Agent graph 进程内缓存 |
| 本地模型 | 自动发现本机 Ollama 模型，通过 OpenAI 兼容接口调用 |
| 模型调用调试 | 对照保存 Provider 完整 HTTP 响应和 LangChain `AIMessage` |
| 数据存储 | SQLite + WAL，聊天记录、轮次、模型调试记录、摘要版本永久保存 |
| Web 服务 | FastAPI JSON API + 原生 HTML/CSS/JavaScript 聊天页面 |

## 仓库定位

- GitHub：<https://github.com/nonoshuaige/XiaoYuanAI>
- 本地目录：`/Users/zypro/Desktop/XiaoYuanAI/XiaoYuanAI`
- Git 远端：`origin` → `https://github.com/nonoshuaige/XiaoYuanAI.git`
- GitHub 推送账号：`nonoshuaige`
- 主分支：`main`

后续开发、提交和推送均以这个本地目录及其 `origin` 远端为准。

## 快速启动

项目使用 Python 3.10+，推荐在独立虚拟环境中运行。

```bash
conda activate agent-env
pip install -r requirements.txt
python server.py
```

浏览器访问 <http://localhost:8000>。

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
浏览器
  │
  ▼
FastAPI API
  │
  ├── 模型目录与 Provider 配置
  ├── 会话增删改查
  └── 聊天请求
        │
        ▼
AgentRuntime
  │
  ├── 按 model ID 加载并缓存模型/graph
  ├── 按 session 串行执行对话轮次
  ├── 从 SQLite 重建模型上下文
  ├── 记录 Provider 响应与 LangChain 转换结果，供调试对照
  ├── 按需调用“找人”工具
  └── 提交异步摘要任务
        │
        ├── OpenAI 兼容模型服务
        └── SQLite 会话与员工通讯录存储
```

## Agent 运行时

主链位于 `agent.py`，生产 Agent 注册了“找人”工具：

```text
收到用户问题
  → 用户消息落库，轮次标记为 pending
  → 从 SQLite 读取最新摘要和未覆盖原文
  → 构建本次临时消息 State
  → 调用选中的模型，按需执行工具
  → 保存 Provider 响应、完整 AIMessage 和 assistant 回复
  → 轮次标记为 completed
  → 必要时提交后台摘要任务
```

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

用户至少需要明确提供工号、手机号或姓名中的一项。若同时提供多项，查询只选择最高
优先级的字段：`工号 > 手机号 > 姓名`；部门可附加为精确过滤条件。工号、手机号、
姓名均为精确匹配。姓名查询得到多人时，工具返回候选而不是擅自选择。

生产 Agent 首次初始化时会自动建表，但不会写入虚构员工。同步员工数据时可调用
`PeopleStore.upsert(...)`；之后 Agent 即可查询同一数据库中的记录。

### 虚构员工沙箱

仓库提供独立的 `data/sandbox.db` 沙箱，不会污染默认的
`data/xiaoyuan.db`。运行以下命令会幂等写入 10 条虚构员工记录并启动网页服务：

```bash
/Users/zypro/Desktop/pythonenv/envs/XiaoYuan/bin/python sandbox.py
```

浏览器访问 <http://127.0.0.1:8000>。只初始化和验证数据、不启动服务：

```bash
/Users/zypro/Desktop/pythonenv/envs/XiaoYuan/bin/python sandbox.py --seed-only
```

可用示例问题：

- `帮我找工号 XY-S003`
- `手机号 13800000004 是谁？`
- `帮我找研发部的陈晨`
- `帮我找张三`（会返回研发部和财务部两位候选）
- `找工号 XY-S003、手机号 13800000001、姓名张三`（按工号优先）

沙箱仍会使用 `.env` 中配置的模型服务；仅 SQLite 数据与默认环境隔离。可通过
`XIAOYUAN_DB_PATH` 为普通启动指定其他数据库。

## 上下文管理

SQLite 是持久化事实来源。每次聊天请求都会重新构建临时消息 State，不依赖进程
内的历史消息缓存。

模型实际收到的上下文顺序为：

```text
SystemMessage
  固定办公助手规则

HumanMessage（可选）
  历史对话摘要，明确标记为“用户层上下文”

HumanMessage / AIMessage
  摘要范围之后的完整原始对话

HumanMessage
  当前用户问题
```

### 权限边界

System Prompt 固定在代码中。历史摘要不会拼接进 System Prompt，也不能覆盖系统
规则或成为外部操作授权。

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
   JSON 解析结果。SDK 重试产生的多个 HTTP 响应会按发生顺序全部保留。
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
| `chat_messages` | 所有 user/assistant 原文 |
| `conversation_summaries` | 累计摘要正文、覆盖范围和历史版本 |

`chat_messages` 使用 `(session_id, round_no, role)` 作为复合主键。摘要只影响模型
上下文的构建方式，不会删除或改写完整聊天记录。

## Web 页面

`static/index.html` 是不依赖前端框架的单页聊天界面，包含：

- 虚拟“新对话”入口；
- 会话列表、轮数和当前会话状态；
- 会话切换、重命名和删除确认弹窗；
- Provider → 模型二级选择器；桌面端悬浮 Provider 展开右侧模型菜单；
- 当前 session 和模型的 `localStorage` 记忆；
- 服务端完整聊天记录恢复。

浏览器只保存当前 `sessionId` 和选中的 model ID，消息正文以 SQLite 为唯一来源。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/models` | 获取当前配置下的模型目录 |
| `GET` | `/api/models?refresh=true` | 强制刷新 Coding Plan 模型目录 |
| `POST` | `/api/chat` | 发送消息并选择模型 |
| `GET` | `/api/sessions` | 获取真实会话列表 |
| `GET` | `/api/sessions/{sessionId}` | 获取会话消息、摘要和压缩状态 |
| `GET` | `/api/sessions/{sessionId}/model-calls` | 获取会话全部模型调用调试记录 |
| `GET` | `/api/sessions/{sessionId}/rounds/{round}/model-call` | 获取指定轮完整模型调用调试记录 |
| `PATCH` | `/api/sessions/{sessionId}` | 重命名会话 |
| `DELETE` | `/api/sessions/{sessionId}` | 删除会话及关联数据 |

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

一次成功响应包含：

```json
{
  "reply": "模型回复",
  "sessionId": "会话 ID",
  "round": 1,
  "title": "自动或手动会话标题",
  "model": "实际使用的 model ID",
  "modelCallUrl": "/api/sessions/会话 ID/rounds/1/model-call"
}
```

## 项目结构

```text
.
├── agent.py                 # Agent 运行时、上下文重建、模型 graph 缓存、异步摘要
├── config.py                # Provider 配置、模型发现、目录状态和模型创建
├── conversation_store.py    # SQLite schema、会话、轮次、消息和摘要持久化
├── model_audit.py           # Provider HTTP 捕获与 AIMessage 完整序列化
├── server.py                # FastAPI 页面与 JSON API
├── sandbox.py               # 虚构员工数据与隔离沙箱启动入口
├── static/
│   └── index.html           # 原生 Web 聊天界面
├── tests/
│   ├── test_agent.py        # 会话、上下文、失败恢复、摘要和懒加载测试
│   ├── test_people_tool.py  # 找人优先级、消歧、校验和 Tool 输出测试
│   ├── test_sandbox.py      # 虚构数据幂等写入与沙箱数据库隔离测试
│   ├── test_config.py       # Provider、模型目录和模型选择测试
│   └── test_model_audit.py  # Provider 原始响应与 AIMessage 序列化测试
├── .env.example             # Provider 配置示例
├── requirements.txt         # Python 依赖
└── README.md
```

## 测试

```bash
python -m unittest discover -s tests -v
```

当前测试覆盖：

- 首次发送后才创建会话；
- 会话隔离、重命名和删除；
- SQLite 重启恢复全量记录；
- 用户消息先落库及失败轮次保留；
- Provider 完整 HTTP 响应与 LangChain `AIMessage` 持久化；
- 摘要保持用户消息权限，不进入 System Prompt；
- 第一次和后续累计滚动摘要；
- 摘要任务阻塞时继续聊天不丢上下文；
- 模型切换及未知模型拒绝；
- Provider 未配置时不出现在目录；
- 模型实例和 Agent graph 按需加载并复用。
- Ollama 模型自动发现、Provider 隔离和本地 OpenAI 兼容地址。

## 当前边界

当前版本仍有以下明确边界：

- 当前只有“找人”一个业务 Tool，尚无通用 Skill Registry 或执行审批；
- 30/20/10 是轮次策略，尚未实现 Token 软阈值和硬上限；
- 摘要尚未记录 prompt 版本、模型、source hash 和 token 用量；
- session 串行锁只在单个 Python 进程内有效；
- 默认面向本地使用，尚未实现用户认证、租户隔离和限流；
- 模型回复为普通请求，尚未实现流式输出。

这些能力应在进入多人或多进程生产部署前补齐。

## 版本

当前版本：**1.4 找人工具与隔离沙箱**
