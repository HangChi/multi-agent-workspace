# Agent 工作台系统设计文档

> 版本：v0.1（初始设计）
> 定位：可上线的完整系统（非 MVP），面向团队/个人自用，后期可扩展为对外服务。

---

## 1. 项目概述

### 1.1 系统定位

一个「拖拽式任务分发 + 多角色 Agent 执行」的工作台系统：

- 用户创建 **Agent**（带角色、提示词、工具、模型配置的"数字员工"），并可自由组合成 **Agent 组**（自定义工作流）。
- 用户把**需求**（任务卡片）拖到某个 Agent 或 Agent 组上，系统立即编排并执行任务。
- Agent 执行过程实时可见（状态机 + 分步日志 + 制品产出），失败可重试、可取消。

### 1.2 目标用户与形态

- 团队/个人自用，部署在自己服务器上。
- 登录认证 + 轻量 RBAC（admin / member / viewer），无多租户隔离负担，但数据模型预留扩展位。

### 1.3 非目标（本期明确不做）

- 面向公众的多租户 SaaS（计费、租户隔离）。
- Agent 自主学习/进化（记忆持久化做简化版，不追求复杂 RAG 知识库）。
- 可视化流程编辑器（组编排先走配置化策略，不做图形化连线编排）。

---

## 2. 核心概念与领域模型

| 概念 | 说明 |
|---|---|
| **Agent（角色）** | 一个可执行任务的"数字员工"：系统提示词、模型、温度、最大步数、可用工具集、成本上限。可继承父 Agent 配置。 |
| **Agent 组** | 用户自定义的 Agent 集合 + 路由策略（直接指定 / 并行分发 / 顺序管道 / 智能路由）。 |
| **需求（Request）** | 用户提交的任务卡片：标题、描述、附件、优先级、期望产出格式、截止时间。 |
| **运行（Run）** | 需求被拖到 Agent/组后产生的一次具体执行，拥有独立生命周期状态机。 |
| **步骤（Step）** | Run 内部的一次原子动作：LLM 调用 / 工具调用 / 子 Agent 调用。Run 由有序 Step 序列构成。 |
| **工具（Tool）** | Agent 可调用的能力单元：LLM 推理、HTTP 请求、脚本/代码执行（沙箱）、MCP 外部工具、批处理命令。 |
| **MCP 服务器** | 注册的外部 MCP 服务，其工具可被 Agent 通过统一协议调用。 |
| **制品（Artifact）** | Run/Step 产出的文件（报告、代码、图片等），存对象存储。 |
| **调度（Schedule）** | 定时/周期触发需求创建与执行的规则。 |

### 2.1 核心关系

```
User 1─N Agent
User 1─N AgentGroup  N─M Agent（组成员）
User 1─N Request
Request 1─N Run
Run 1─N Step
Run 1─N Artifact
Agent N─M Tool
User 1─N Schedule ─N Request(模板化)
```

---

## 3. 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                        Web 前端（React SPA）                    │
│  看板(拖拽) / Agent 配置 / 运行详情(实时日志) / 仪表盘 / 管理后台   │
└──────────────────────────────┬───────────────────────────────┘
                               │ HTTPS / WebSocket
┌──────────────────────────────▼───────────────────────────────┐
│                  API 层：FastAPI（uvicorn 多进程）               │
│  认证/RBAC · Agent·组·需求 CRUD · Run 操作 · WS 实时推送 · 配额   │
└──────────────────────────────┬───────────────────────────────┘
                               │ 任务入队 / 状态回调 / 事件发布
┌──────────────────────────────▼───────────────────────────────┐
│              执行引擎：Celery Workers（可横向扩展）              │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐             │
│  │LLM 执行器│ │代码沙箱  │ │MCP 执行器│ │批处理    │  ← 编排器     │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘  (状态机/策略) │
└──────────────────────────────┬───────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────┐
│       基础设施：PostgreSQL · Redis · MinIO · Docker 沙箱        │
│       可观测：Prometheus + Grafana · structlog · Sentry         │
└──────────────────────────────────────────────────────────────┘
```

### 3.1 Feature 003 外部 MCP 边界

外部 MCP 工具不写入内置 `CATALOG` 或 `Agent.tools`。服务器、发现版本、市场 listing、安装、Agent grant 与 `RunExternalToolSnapshot` 使用独立模型；Worker 在 ReActRunner 中注入快照版本的 `ExternalToolExecutor`。调用前重新校验发布、安装、授权、凭证、endpoint 和权限状态，凭证只在 Worker 请求边界解密。调用结果和错误分类通过同一 Run timeline、invocation projection 与管理员审计查询呈现，原始参数、远端结果和凭证值不会进入通用步骤、事件、日志或 UI。服务器并发使用 Redis 租约，取消和终态 Run 丢弃迟到结果。

003 v1 仅支持经过 HTTPS/SSRF 校验的 Streamable HTTP MCP；不支持 stdio、任意第三方代码或公开应用商店。Feature 002 的 native Linux/full-stack acceptance 仍是独立前置依赖，本特性不包含其修复。

### 3.2 分层职责

1. **Web 层**：React SPA，核心交互是「需求卡片拖到 Agent/组卡片」触发分配；运行详情页通过 WebSocket 看实时日志。
2. **API 层**：FastAPI，无状态 REST + JWT；业务校验、权限校验、配额检查；入队请求只写"创建 Run + 发事件"，重活全部交给执行引擎，保证 API 快速响应。
3. **执行引擎层**：Celery Worker 池（`-c` 按机器配置），负责 Run 的实际执行。编排器驱动状态机，按策略调度执行器；执行器各自抽象统一 `execute(ctx)` 接口。
4. **基础设施层**：PostgreSQL 持久化；Redis 做队列/缓存/分布式锁/发布订阅（状态变更实时推送）；MinIO 存附件与制品；Docker 提供代码执行沙箱（每任务一次性容器）。

---

## 4. 技术选型

| 领域 | 选型 | 理由 |
|---|---|---|
| 后端框架 | FastAPI + Pydantic v2 | 异步性能好、类型安全、自动 OpenAPI 文档 |
| ORM / 迁移 | SQLAlchemy 2.0 (async) + Alembic | 成熟稳定，async 支持完善 |
| 任务队列 | Celery 5 + Redis（Beat 做定时） | 上百并发横向扩展、优先级队列、重试/幂等开箱即用；生态最成熟 |
| LLM 网关 | LiteLLM | 统一 OpenAI/Claude/Gemini/DeepSeek 等模型，自带成本统计与重试 |
| Agent 编排 | 自研轻量编排器（状态机 + ReAct 工具循环） | 任务形态多样（非纯对话），自研可控性强，避免框架锁死 |
| 代码沙箱 | Docker（一次性容器 + 资源限额） | 隔离可靠，成本可控 |
| 数据库 | PostgreSQL 16 | 结构化核心数据 + JSONB 灵活字段 |
| 缓存/队列 | Redis 7 | 队列、缓存、分布式锁、Pub/Sub 一站式 |
| 对象存储 | MinIO（S3 兼容） | 附件/制品存储，可平滑迁移到云 OSS/COS |
| 实时通信 | WebSocket（FastAPI 原生） | 运行日志/状态流式推送 |
| 前端 | React 18 + TS + Vite + TailwindCSS | 工程化成熟、生态好 |
| 拖拽 | dnd-kit | 现代、可访问性友好、支持看板拖放 |
| 状态管理 | Zustand + TanStack Query | 轻量且适合服务端状态缓存 |
| 监控 | Prometheus + Grafana | API/Worker 指标、LLM 成本、队列深度 |
| 日志 | structlog（结构化，request_id/run_id 贯穿） | 便于检索与关联分析 |
| 错误监控 | Sentry（可选） | 生产异常兜底 |
| 认证 | JWT（access + refresh）+ 邀请注册 | 团队自用够用，预留 OIDC 扩展 |
| 部署 | Docker Compose + Caddy(HTTPS) | 单机多容器即可支撑上百并发，后期可拆 K8s |

> **关键决策说明**
> 1. **自研编排器而非 LangGraph**：系统面向"任意类型任务"（写文章、跑脚本、调 API、批处理），LangGraph 偏对话流编排；自研状态机 + 策略模式更贴合"需求 → 执行 → 产出"的产品形态，且不依赖特定框架的运行时。
> 2. **Celery 而非 Arq/Dramatiq**：虽然执行器内部是 async，但 Celery 的重试、优先级、Beat、Worker 管理生态最成熟，适合"完整可上线"目标；LLM 调用封装为 async 并在执行器内统一管理事件循环。
> 3. **每个 Run 一个代码沙箱容器**：隔离性强于子进程，满足"执行任意用户脚本"的安全底线，容器复用缓存可提速。

---

## 5. 执行引擎设计

### 5.1 任务生命周期状态机

```
                 ┌──────────────────────────────────────────┐
                 │                                          │
  created ──► assigned ──► queued ──► running ──► completed
     │            │            │         │  ▲        │
     │            │            │         │  │        ├──► failed ──► retry
     ▼            ▼            ▼         ▼  │        │
  cancelled ◄────┴────────────┴─────────┴───┴────────┘        │
                                   needs_input ─────────────► 等待用户补充 ──► running
```

- `created`：需求创建（未分配）。
- `assigned`：用户拖拽到 Agent/组，生成 Run 并校验配置。
- `queued`：Run 入队（按优先级队列）。
- `running`：编排器驱动执行（LLM 循环 / 组策略分发）。
- `needs_input`：Agent 需要用户补充信息（可暂停等待，用户回复后恢复）。
- `completed` / `failed` / `cancelled` / `timeout`：终态。
- `failed` 可手动或自动重试（重新入队，幂等）。

### 5.2 单 Agent 执行循环（ReAct 风格）

```
while step < max_steps and not done:
    1. 组装 messages（system_prompt + 历史 + 本轮上下文 + 工具 schema）
    2. LLM 推理（LiteLLM，记录 token/成本）
    3. 若输出 tool_call：
       - 校验工具权限 → 执行（HTTP/脚本沙箱/MCP）
       - 结果回填上下文，记录 Step（kind=tool）
       - 循环继续
    4. 若输出最终答案：
       - 解析为结构化结果（文本/JSON/制品引用），记录 Step，Run 完成
```

要点：
- 每步有超时（默认 120s，可配）；全局超时（默认 30min，可配）。
- LLM 5xx / 限流自动重试（指数退避）；工具瞬时错误（网络）自动重试 1-2 次；业务错误不重试、进 `failed`。
- 成本实时累计，超 Agent 成本上限自动终止（防失控）。

### 5.3 组编排策略

| 策略 | 语义 | 适用场景 |
|---|---|---|
| `direct`（直接指定） | 拖到具体 Agent，单 Agent 执行 | 默认，最常用 |
| `fan_out`（并行分发） | 组内所有 Agent 并行执行同一需求，各自产出，结果汇总面板 | 多角度分析、A/B 多方案 |
| `pipeline`（顺序管道） | 按组内成员顺序串联，前一个的输出作为后一个的输入 | 写作→审校、需求→设计→实现 |
| `router`（智能路由） | LLM 根据需求内容判断分发给组内最合适的 Agent（可指定候选集） | 不明确该给谁时"自动分配" |

- 组编排产生**一个父 Run + 多个子 Run**（每个 Agent 一个），子 Run 复用同一状态机，父子通过 `parent_run_id` 关联。
- 用户在拖拽确认弹窗中可选策略（组默认策略可配置）。

### 5.4 队列与 Worker 设计（支撑上百并发）

- 优先级队列：`high` / `normal` / `low` 三个 Celery 队列，需求优先级映射。
- Worker 按类型分组：`llm`、`sandbox`、`batch`（避免沙箱重任务阻塞 LLM 轻任务）。
- API 层只做入队，`assign` 接口 RT < 100ms；压力全在 Worker 侧，可独立扩 Worker 容器副本。
- 分布式锁：同一 Run 的幂等（Redis 锁 + DB 唯一约束 `run_id`），防重复执行/重复回调。
- 回调机制：Worker 完成/失败后通过事件总线（Redis Pub/Sub → API 进程）推送状态，WebSocket 广播给前端。

### 5.5 工具协议（统一抽象）

```python
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict          # JSON Schema
    kind: Literal["llm", "http", "script", "mcp", "batch"]

class ToolExecutor(Protocol):
    async def execute(self, ctx: RunContext, args: dict) -> ToolResult: ...
```

- `http`：带凭证管理的 HTTP 请求（凭证加密存储，不落日志）。
- `script`：代码沙箱执行（Python/Shell/JS 模板）。
- `mcp`：动态发现已注册 MCP 服务器的工具，统一转成 ToolSpec。
- Agent 配置时勾选可用工具集；工具使用记入审计日志。

---

## 6. 安全设计

| 风险 | 对策 |
|---|---|
| 任意代码执行 | 代码沙箱强制 Docker 一次性容器：CPU/内存/磁盘限额、执行超时、默认无外网（可配白名单域名）、输入只读挂载、容器即用即毁；禁 root、禁特权 |
| 凭证泄露 | 工具凭证用 Fernet 对称加密落库，密钥走环境变量/KMS；日志脱敏（不打印 Authorization、密钥字段） |
| 越权访问 | RBAC 三角色：admin（全量）/ member（管理自己的 Agent 组与需求）/ viewer（只读）；资源归属校验 |
| 成本失控 | 每 Agent 成本上限 + 每用户月配额 + 全局并发上限；LiteLLM 实时成本统计，超限自动熔断 |
| 审计缺失 | audit_logs 全量记录：登录、Agent/组变更、工具使用、Run 操作、凭证修改 |
| LLM 注入 | 系统提示词强调工具调用校验；对外部输入（网页抓取内容等）做边界标记，限制指令注入面 |

---

## 7. API 设计概要（v1）

```
# 认证
POST /api/v1/auth/register          # 邀请码注册（团队自用）
POST /api/v1/auth/login             # 邮箱 + 密码 → JWT
POST /api/v1/auth/refresh

# Agent / 组
GET|POST   /api/v1/agents           PATCH|DELETE /api/v1/agents/{id}
GET|POST   /api/v1/groups           PATCH|DELETE /api/v1/groups/{id}
POST       /api/v1/groups/{id}/members        # 增删组成员
GET|POST   /api/v1/tools            PATCH|DELETE /api/v1/tools/{id}
GET|POST   /api/v1/mcp-servers      PATCH|DELETE /api/v1/mcp-servers/{id}

# 需求与分配（核心交互）
POST       /api/v1/requests                    # 创建需求（含附件上传）
GET        /api/v1/requests?status=&q=         # 看板列表
POST       /api/v1/requests/{id}/assign        # 拖拽落点：{assignee_type: agent|group, assignee_id, strategy?}
POST       /api/v1/requests/{id}/attachments   # 附件上传（预签名 URL → MinIO）

# 运行
GET        /api/v1/runs/{id}                   # 详情（状态、步骤、成本、错误）
POST       /api/v1/runs/{id}/cancel
POST       /api/v1/runs/{id}/retry
GET        /api/v1/runs/{id}/artifacts         # 制品下载（预签名）
WS         /api/v1/ws/runs/{id}                # 实时状态 + 分步日志流

# 调度 / 统计
GET|POST   /api/v1/schedules      PATCH|DELETE /api/v1/schedules/{id}
POST       /api/v1/schedules/preview           # 预览下次触发（200 + error，不 4xx）
GET        /api/v1/schedules/{id}/triggers     # 触发历史（分页 + 过滤 + 全量 counts）
GET        /api/v1/stats/overview              # 仪表盘：运行中/队列深度/成功率/成本趋势
```

---

## 8. 数据模型（核心表）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `users` | id, email, password_hash, role, status, quota_json | RBAC 角色 |
| `agents` | id, name, description, system_prompt, model, temperature, max_steps, tools(jsonb), parent_id, cost_limit, owner_id | 角色 Agent |
| `agent_groups` | id, name, description, routing_strategy, owner_id | 组 + 默认策略 |
| `group_members` | group_id, agent_id, position | 组成员有序（pipeline 用） |
| `requests` | id, title, description, priority, deadline, attachments(jsonb), expected_output, owner_id, status | 需求卡片 |
| `runs` | id, request_id, parent_run_id, assignee_type, assignee_id, strategy, status, current_step, result(jsonb), error, cost, timestamps | 执行实例 |
| `steps` | id, run_id, seq, kind, input, output, tool_name, duration_ms, cost, status, error | 分步记录 |
| `tools` | id, name, kind, config_encrypted, sandbox_level, owner_id | 工具定义 |
| `mcp_servers` | id, name, url, config_encrypted, enabled | MCP 注册 |
| `artifacts` | id, run_id, step_id, object_key, filename, size, mime | 制品 |
| `schedules` | id, owner_id, name, cron_expr, timezone, template_*(title/description/expected_output/priority), target_type, target_id, target_strategy, enabled, next_run_at, last_run_at, last_result, consecutive_failures, auto_disabled_reason | 定时规则 |
| `schedule_triggers` | id, schedule_id, planned_at, fired_at, result(fired/missed/skipped_busy/failed), reason, missed_count, window_start, window_end, request_id, run_id · UNIQUE(schedule_id, planned_at) | 触发事实（不可变） |
| `audit_logs` | id, user_id, action, target_type, target_id, detail, created_at | 审计 |

索引要点：`runs(status, created_at)`、`runs(request_id)`、`steps(run_id, seq)`、`requests(status, priority)`、`schedules(next_run_at) WHERE enabled`（部分索引，扫描只走启用的规则）、`schedule_triggers(schedule_id, planned_at DESC, id DESC)`。

**调度的两个语义要点**（完整论证见 `specs/004-scheduled-requests/`）：

1. **周期表达式取交集。** 本项目用的 Celery 5.7.0 在「日期」与「星期」同时限定时取交集
   （`0 9 1 * 1` 只在「既为 1 号又是周一」触发），与标准 cron 的并集不同。
   规格采用交集并在 `/schedules` 页的 cron 输入框下方明示——这比自研一套 cron 解析器
   更不易出错，代价是与部分用户的直觉不同。
2. **错过不补跑，但必须记清楚。** 扫描发现 `lag > 90s` 时合并记一条 `missed`，
   `missed_count` 是跨过的计划时刻数（实测停机 5 分钟 → 7 次）。
   这条判定**必须早于**「前次未结束」——否则停机恢复后会被记成 `skipped_busy`，
   用户看到"被跳过"而非"错过了 N 次"，真正的运维事实被掩盖（已用反向控制实测证明）。
3. **幂等靠唯一约束先占位。** 先 `INSERT ... ON CONFLICT DO NOTHING RETURNING id` 占位，
   插得进去才继续建单。反过来（先建单再写记录）会在进程崩溃时留下"需求已存在但记录不存在"，
   唯一约束拦不住。占位法的失败模式是"占位了但没建单"，即漏跑一次，符合总体取舍。

---

## 9. 前端设计

### 9.1 页面结构

| 页面 | 内容 |
|---|---|
| **工作台（核心）** | 左侧需求列（拖拽源，按状态分组），中间 Agent/组画布（拖放目标，卡片显示 Agent 头像/角色/在线状态），拖放后弹出确认（选策略） |
| **Agent 管理** | Agent 创建/编辑：提示词、模型选择、温度、工具勾选、成本上限；支持从现有 Agent 派生 |
| **Agent 组管理** | 组创建/编辑：成员增删排序、路由策略配置 |
| **运行详情** | 状态机时间线、Step 列表（每次 LLM/工具调用的输入输出可展开）、实时日志流（WebSocket）、制品下载、取消/重试按钮 |
| **仪表盘** | 运行中任务、队列深度、成功率、失败分布、成本趋势（近 7/30 天） |
| **管理后台** | 用户/角色管理、MCP 服务器注册、工具管理、配额配置 |

### 9.2 拖拽交互设计

- 需求卡片（dnd-kit `useDraggable`）→ 拖到 Agent 卡或组卡（`useDroppable`）。
- 落点高亮 + 组卡展开成员预览；放置后弹确认框：显示目标、可选策略（组默认策略预选）、优先级。
- 交互数据流：`POST /requests/{id}/assign` → 乐观更新卡片状态 → WebSocket 推送真实状态。

### 9.3 实时性

- TanStack Query 做服务端状态缓存（列表/详情），WebSocket 只负责增量事件（状态变更、新 Step、日志行、成本更新）。
- 日志流节流合并（每 200ms 批量推送一次），避免高频刷新。

---

## 10. 可观测性与监控

- **日志**：structlog，全链路 `request_id` / `run_id` / `step_id` 贯穿 API 与 Worker；错误日志含堆栈与上下文。
- **指标（Prometheus）**：
  - API：请求量、延迟分位、错误率、活跃 WS 连接数。
  - Worker：队列深度、任务处理时长、重试率、失败率。
  - LLM：调用数、token 用量、延迟、成本（按模型/按 Agent 维度）。
  - 沙箱：容器启动耗时、执行时长、被限额终止数。
- **告警**：队列积压超阈值、失败率突增、LLM 成本日超限、沙箱容器异常。
- **Sentry**：未捕获异常自动上报（可关）。

---

## 11. 部署架构

```
Caddy (HTTPS 自动证书)
   │
   ├── api: uvicorn x N（Nginx 内部负载可选）
   ├── worker-llm / worker-sandbox / worker-batch（Celery，可扩副本）
   ├── beat（定时调度）
   ├── postgres:16（数据卷 + 定时 pg_dump 备份）
   ├── redis:7（AOF 持久化）
   ├── minio（附件/制品，跨容器共享）
   └── sandbox 节点（Docker in Docker 或独立 Docker socket，仅接受编排器指令）
```

- 单机 Docker Compose 起步，可支撑数百并发（取决于机器）；压力大时拆出 worker 独立节点。
- 环境配置走 `.env`（Compose 注入），密钥不落镜像、不入库。
- 备份：每日 pg_dump + 对象存储同步；恢复演练纳入上线 checklist。

---

## 12. 分阶段实施计划（每阶段结束均可上线）

### Phase 1 — 核心闭环（可上线）
认证/RBAC · Agent 与组 CRUD · 需求创建与看板 · **拖拽分配** · 单 Agent LLM 执行（ReAct 循环）· 任务状态机 · WebSocket 实时日志 · 制品上传/下载 · 仪表盘雏形

**验收**：一个需求从拖拽到产出报告的全流程可用，运行过程实时可见。

### Phase 2 — 引擎强化
组编排三策略（fan_out / pipeline / router）· 代码沙箱执行器 · MCP 服务器接入 · 重试/超时/取消完善 · 成本统计与配额熔断 · **定时调度（Celery Beat，已实现）**

**验收**：任意类型任务（写文章、跑脚本、调外部 API）均可通过 Agent/组执行，成本可控。

### Phase 3 — 生产化
Prometheus/Grafana 监控告警 · 审计日志完善 · 备份演练 · CI/CD（GitHub Actions：lint + 测试 + 镜像构建 + 部署）· 压测（上百并发）· 安全加固复核

**验收**：系统稳定运行、故障可发现可回溯、扩容操作文档化。

**当前状态（2026-11-17）**：Phase 1 / Phase 2 已完成并有 CI 门禁（含原生 Linux 与真实栈验收）。
Phase 3 进展：
- ✅ CI（lint + 测试 + 镜像构建）+ 手动触发的部署 job + `deploy/deploy.sh`（健康门禁/回滚）
- ✅ Prometheus 指标（`/metrics`，含 Worker 侧跨进程合并）与 8 条告警规则（阈值待在真实流量下校准）
- ✅ 备份脚本 + 恢复演练脚本（演练只在开发机验证）
- ✅ 上百并发压测（100/200 并发，0 错误；延迟受 2 vCPU 开发机限制）
- ⏳ 仍缺：Grafana 面板、Sentry 上报、PITR、生产环境部署 smoke、
  以及 `worker` 挂载 `docker.sock`（≈宿主 root）的安全加固复核

---

## 13. 目录结构（规划）

```
multi-agent-workspace/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── api/                 # 路由层（auth/agents/groups/requests/runs/ws/...）
│   │   ├── core/                # 配置、安全(JWT/Fernet)、依赖注入、审计
│   │   ├── models/              # SQLAlchemy 模型
│   │   ├── schemas/             # Pydantic 模型
│   │   ├── services/            # 业务逻辑（分配、配额、成本）
│   │   ├── orchestrator/        # 编排核心：state_machine / strategies / runner
│   │   ├── executors/           # 执行器：llm / http / script_sandbox / mcp / batch
│   │   └── tasks/               # Celery 任务定义
│   ├── alembic/                 # 数据库迁移
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── api/                 # API 客户端
│   │   ├── components/          # board(拖拽) / agent / group / run / dashboard / admin
│   │   ├── stores/              # Zustand
│   │   ├── hooks/               # useRunSocket 等
│   │   └── pages/
│   ├── package.json
│   └── vite.config.ts
├── deploy/
│   ├── docker-compose.yml
│   ├── Caddyfile
│   └── .env.example
├── docs/                        # 本文档 + 后续详细设计
└── README.md
```

---

## 14. 风险与开放问题

1. **沙箱安全性**：代码执行的安全底线依赖 Docker 配置正确性，Phase 3 需专项加固（禁用特权、seccomp、网络白名单），并考虑 gVisor 升级路径。
2. **LLM 成本**：上百并发下 token 成本可能显著，配额/熔断机制需在 Phase 2 优先落地。
3. **编排器能力边界**：自研编排器在复杂多 Agent 协作（如多轮相互评审）上需逐步演进，Phase 2 结束后评审是否需要引入图执行引擎。
4. **单机 vs 集群**：单机 Compose 撑上百并发需较高配置（建议 ≥8C16G）；若超预期可平滑拆分 worker 节点。

> 本文档为 v0.1 初始设计，随实现迭代持续更新。
