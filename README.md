# 小原 AI 助手 1.1｜多轮次对话

小原 AI 助手是一个面向中文办公场景的单 Agent 对话应用。本版本聚焦稳定的
多轮次对话体验：支持多会话、SQLite 全量持久化、滚动摘要、异步上下文压缩，
暂不加载业务 Tool 和 Skill。

## 操作方面

### 启动项目

项目使用已有的 Conda Agent 环境：

```bash
conda activate agent-env
pip install -r requirements.txt
python server.py
```

浏览器访问 <http://localhost:8000>。

模型配置从项目根目录的 `.env` 读取：

```dotenv
DASHSCOPE_API_KEY=你的密钥
MODEL_NAME=qwen3-coder-plus
OPENAI_API_BASE=https://coding.dashscope.aliyuncs.com/v1/
```

`.env` 已加入 `.gitignore`，不会提交到 Git。

### 新建对话

侧栏顶部始终有一个固定的“新对话”入口。

- 点击“新对话”只会打开空白聊天页面。
- 此时不会写入 SQLite，也不会生成 `sessionId`。
- 用户发送第一条消息后，后端才创建真实会话并保存第一轮。
- 第一条用户消息的首句会自动成为会话名称，最长 24 个字符。

因此无论点击多少次“新对话”，数据库都不会出现空白会话。

### 切换会话

侧栏按最近更新时间展示所有真实会话，并显示当前轮数。点击任意会话即可读取
SQLite 中保存的完整聊天记录。浏览器只使用 `localStorage` 记住当前选中的
`sessionId`，聊天数据以 SQLite 为唯一事实来源。

### 重命名会话

点击会话右侧的编辑按钮后，标题会在原位置变成输入框：

- 自动聚焦，光标位于标题末尾；
- `Enter` 保存；
- `Esc` 取消；
- 点击其他位置自动保存；
- 空名称不会提交；
- 手动名称不会被后续消息重新覆盖。

### 删除会话

点击删除按钮会打开页面内的确认弹窗，而不是浏览器原生提示。

- 弹窗显示即将删除的会话名称；
- 明确提示聊天记录和摘要会一起删除；
- 支持取消、确认删除、`Esc` 和点击遮罩关闭；
- 删除进行中不能重复操作；
- 删除失败会在弹窗内显示原因。

确认后，SQLite 会级联删除该会话的全部消息与摘要版本。

## 内核方面

### 单 Agent 主链

当前使用一个无工具 Agent：

```text
用户 Query
  → SQLite 先保存 user，轮次状态为 pending
  → 从 SQLite 重建此前上下文
  → 固定 System Prompt
  → 用户层 Summary + 未覆盖原始轮次 + 当前 Query
  → LangChain create_agent（tools=[]）
  → 模型回复
  → SQLite 保存 assistant，轮次状态改为 completed
  → 必要时提交异步摘要任务
```

如果模型调用失败，user 原文不会丢失，轮次状态改为 `failed` 并记录错误；
该轮不会伪造 assistant 消息。

当前没有能力路由、任务状态、Tool Runtime 或 Skill Registry。

### 上下文权限边界

System Prompt 始终使用代码中固定的办公助手规则，不会拼接历史摘要，也不会被
摘要、最近对话或当前 Query 修改。

模型实际收到的层级为：

```text
SystemMessage：固定 System Prompt
HumanMessage：历史摘要（如有，明确标记为用户层上下文）
HumanMessage / AIMessage：摘要范围之后的原始对话
HumanMessage：当前 Query
```

历史摘要开头会明确说明：它只是恢复背景的数据，不是系统指令，不能覆盖系统
规则，也不能作为执行外部操作的授权依据。

`SUMMARY_SYSTEM_PROMPT` 只用于独立的摘要生成请求，负责告诉摘要模型如何压缩
历史；生成出来的摘要回到主 Agent 时仍然只是 `HumanMessage`。

### 会话生命周期

`sessionId` 是真实会话的唯一标识。首次请求不传 `sessionId` 时，服务生成一个
新 ID；之后同一会话的请求持续使用该 ID。

一次完整轮次由两条消息组成：

```text
第 N 轮
├── user
└── assistant
```

轮次状态变化：

```text
pending   用户消息已持久化，模型尚未完成
completed 用户和助手消息均已持久化
failed    用户消息已保留，但模型没有生成可用回复
```

同一 session 内的模型请求会串行执行，避免并发请求造成轮次顺序错乱；不同
session 可以独立处理。

### SQLite 数据结构

数据库运行时生成于 `data/xiaoyuan.db`，使用 WAL 模式。

#### `sessions`

| 字段 | 说明 |
|---|---|
| `session_id` | 会话主键 |
| `title` | 会话名称 |
| `round_count` | 已接收的用户轮次数 |
| `created_at` | 创建时间 |
| `updated_at` | 最近更新时间 |

`round_count` 在写入轮次时同步更新，会话列表直接读取 `sessions`，不需要扫描
消息表统计轮数。

#### `conversation_rounds`

保存每轮请求的生命周期：

| 字段 | 说明 |
|---|---|
| `session_id` | 所属会话 |
| `round_no` | 会话内轮次 |
| `status` | `pending`、`completed` 或 `failed` |
| `error` | 失败原因，成功时为空 |
| `created_at` | 用户消息落库时间 |
| `completed_at` | 成功或失败结束时间 |

#### `chat_messages`

永久保存所有 user/assistant 原文。复合主键为：

```text
(session_id, round_no, role)
```

摘要压缩不会删除全量聊天记录。

#### `conversation_summaries`

保存每一版累计摘要及对应原始轮次范围：

| 字段 | 说明 |
|---|---|
| `session_id` | 所属会话 |
| `content` | 摘要正文 |
| `start_round` | 覆盖起始轮次 |
| `end_round` | 覆盖结束轮次 |
| `created_at` | 生成时间 |

历史摘要版本会保留，例如：

```text
摘要 1：第 1–20 轮
摘要 2：第 1–40 轮
摘要 3：第 1–60 轮
```

### 30/20/10 滚动摘要

摘要尚未覆盖的原始记录达到 30 轮时，后台异步压缩最早 20 轮，保留最近
10 轮原文作为活跃上下文。

首次压缩：

```text
完整记录：第 1–30 轮
摘要覆盖：第 1–20 轮
活跃原文：第 21–30 轮
```

再次达到 30 轮未压缩上下文时：

```text
旧摘要：第 1–20 轮
新增压缩：第 21–40 轮
新摘要：第 1–40 轮
活跃原文：第 41–50 轮
```

### 异步压缩期间不丢上下文

摘要任务不会阻塞用户继续发送消息。每次模型调用都会先读取最新摘要的
`end_round`，再从全量消息表读取该轮次之后的所有记录：

```text
模型上下文
  = 最新摘要
  + round_no > summary.end_round 的全部原始记录
  + 当前 Query
```

例如摘要仍只覆盖第 1–20 轮，而用户已经发送到第 35 轮，模型会收到：

```text
第 1–20 轮摘要 + 第 21–34 轮原文 + 第 35 轮当前 Query
```

因此压缩变慢只会临时增加上下文长度，不会遗漏消息。每个 session 同时最多
运行一个压缩任务；任务结束后如果仍积压 30 轮，会自动继续追赶。

### Agent 临时 State

SQLite 是持久化事实来源，每次请求临时重建消息列表：

```python
{
    "messages": [
        HumanMessage("历史摘要；用户层上下文"),  # 有摘要时
        # 摘要范围之后的原始 HumanMessage / AIMessage
        HumanMessage("当前 Query"),
    ],
}
```

System Prompt 由 Agent 配置单独提供，不属于上述可变消息列表。当前 Query 已经
先写入 SQLite，但构建模型输入时只添加一次，不会因持久化而重复。

`sessionId` 属于运行边界和数据库主键，不作为模型可生成的业务状态。

### 待实现方案：Token 硬上限

当前 `30/20/10` 仍是轮次策略，尚未实现 Token 计数和硬上限。计划保留轮次
规则，同时增加两级 Token 保护：

```text
可用历史预算
  = 模型上下文上限
  - 最大输出预留
  - 固定 System Prompt
  - 未来 Tool/Skill 预留
  - 安全余量

达到软阈值：提前提交异步摘要
达到硬阈值：当前请求不能继续等待后台任务，执行同步应急压缩
```

触发条件计划调整为：

```python
should_compress = (
    uncovered_rounds >= 30
    or uncovered_tokens >= soft_token_limit
)
```

具体 Token 阈值需要根据实际模型窗口、真实对话 P90/P95 长度、最大回复长度和
未来 Tool 返回量通过评测确定，本版本不写死。

### 待实现方案：摘要版本与生成信息

本版本仍只持久化摘要正文、覆盖轮次和创建时间。计划为每版摘要补充：

| 字段 | 用途 |
|---|---|
| `prompt_version` | 标识使用的摘要 Prompt 版本 |
| `model_name` | 标识生成摘要的模型 |
| `source_hash` | 校验摘要对应的原始范围 |
| `input_tokens` | 记录摘要输入成本 |
| `output_tokens` | 记录摘要输出成本 |
| `status` | 记录生成状态 |
| `error` | 记录失败原因 |

这些字段用于评测、审计和重新生成摘要，当前只保留方案，尚未修改数据库结构。

### API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/chat` | 发送消息；首次不传 `sessionId` |
| `GET` | `/api/sessions` | 获取全部真实会话 |
| `GET` | `/api/sessions/{sessionId}` | 获取会话、全量消息和摘要状态 |
| `PATCH` | `/api/sessions/{sessionId}` | 重命名会话 |
| `DELETE` | `/api/sessions/{sessionId}` | 删除会话 |

首次发送示例：

```json
{
  "message": "帮我整理今天的会议纪要"
}
```

继续对话：

```json
{
  "message": "再精简一点",
  "sessionId": "服务返回的会话 ID"
}
```

### 项目结构

```text
.
├── agent.py               # Agent、上下文重建和异步摘要
├── conversation_store.py  # SQLite 会话、消息和摘要存储
├── config.py              # 模型配置
├── server.py              # FastAPI 与会话接口
├── static/index.html      # 多会话聊天页面
├── tests/test_agent.py    # 持久化、隔离和压缩测试
├── requirements.txt
└── .env.example
```

### 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖：

- 首次发送创建会话；
- 会话隔离、删除和重命名；
- SQLite 重启恢复；
- 用户消息先落库以及失败轮次保留；
- 摘要保持用户消息权限，不进入 System Prompt；
- 第一次与第二次滚动压缩；
- 摘要任务阻塞时继续对话不丢上下文；
- 全量记录在压缩后仍完整保留。

## 版本

当前版本：**1.1 多轮次对话**
