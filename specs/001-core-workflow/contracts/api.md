# Contracts: 核心工作流（Core Workflow）

**Phase 1 产出**：对外 API 契约（REST + WebSocket）。对齐 spec FR 与 data-model。

**约定**：Base URL `/api/v1`；JSON；Bearer JWT（除 auth 外）；错误统一 `{"error": {"code": "...", "message": "..."}}`。

## 认证

### POST /auth/register
注册（邀请码）。请求：
```json
{"email": "u@x.com", "password": "***", "invite_code": "ABC123"}
```
201 → `{"user": {"id": "...", "email": "...", "role": "member"}, "tokens": {"access": "...", "refresh": "..."}}`
- 400 邀请码无效/过期/用尽；409 邮箱已存在。

### POST /auth/login
登录。请求 `{"email", "password"}` → 200 tokens（同上）。403 凭证错误/账号禁用。

### POST /auth/refresh
刷新。请求 `{"refresh_token"}` → 200 `{"tokens": {"access": "...", "refresh": "..."}}`（与 register/login 同构，前端按 `data.tokens` 解包）。

### GET /auth/me
当前用户 → 200 `{"id", "email", "role", "status"}`。

## Agent

### GET /agents
列表（自己 + 可用的）→ 200 `{"items": [Agent], "total": n}`。查询参数 `?status=active&q=`。

### POST /agents
创建。请求：
```json
{"name": "...", "description": "...", "system_prompt": "...", "model": "gpt-4o-mini",
 "temperature": 0.7, "max_steps": 10, "max_run_seconds": 1800,
 "tools": ["web_search"], "cost_limit_cents": 10000}
```
201 → Agent（含 id/owner_id/created_at）。

### PATCH /agents/{id}
编辑（归属校验：非 owner 回 404，与「不存在」同响应；见 016）。请求部分字段。→ 200 Agent。

### POST /agents/{id}/archive
归档。→ 200 `{"status": "archived"}`。被组引用的 Agent 仅可归档不可删除。

### DELETE /agents/{id}
删除（无组引用时）。→ 204。409 有组引用。

## Agent 组

### GET /groups
列表 → 200 `{"items": [GroupWithMembers], "total": n}`。

### POST /groups
创建。请求 `{"name", "description", "routing_strategy"}` → 201 Group。

### PATCH /groups/{id}
编辑（归属校验）。→ 200。

### POST /groups/{id}/members
添加成员。请求 `{"agent_id": "...", "position": 1}` → 201。409 已存在。

### DELETE /groups/{id}/members/{agent_id}
移除成员。→ 204。

### DELETE /groups/{id}
删除（级联清空成员）。→ 204。

**GroupWithMembers**:
```json
{"id": "...", "name": "...", "description": "...", "routing_strategy": "fan_out",
 "members": [{"agent_id": "...", "name": "...", "position": 1}], "status": "active"}
```

## 需求

### GET /requests
看板列表 → 200 `{"items": [Request], "total": n}`。参数：`?status=&q=&priority=&page=&page_size=`。

列表与详情都带 `runs_summary`（该需求名下 Run 的结果构成，未分配时为 `null`）：
`{"total": n, "completed": n, "failed": n, "cancelled": n, "active": n}`。
需求状态按 **失败 > 取消 > 完成** 收敛（任一 Run 失败即需求失败，不静默当完成），
`runs_summary` 就是用来让界面说清「5 个里 3 个完成、1 个失败」，而不是只给一个红色「失败」。

### POST /requests
创建。请求 `{"title", "description", "expected_output", "priority"}` → 201 Request（status=created）。

### GET /requests/{id}
详情 → 200 Request（含 attachments）。

### POST /requests/{id}/attachments
上传附件（multipart，预签名前）→ 201 `{"attachments": [{"key","name","size","mime"}]}`。

### POST /requests/{id}/assign
**核心交互（FR-010~013）**。请求：
```json
{"assignee_type": "agent|group", "assignee_id": "...", "strategy": "direct|fan_out|pipeline|router"}
```
- 拖到单个 Agent：`strategy` 可省（默认 direct）。
- 拖到组：`strategy` 可省（默认取组配置）。
- 202 → `{"run_id": "...", "status": "queued"}`（受理入队）。
- 409 需求已在执行（FR-012）；404 目标不存在/无权限；400 目标已归档 / 组内无可用成员 / 组类策略拖到单个 Agent。

**四种策略的 Run 结构**

| 策略 | 建 Run 数 | 执行方式 |
|---|---|---|
| direct | 1 | 直接交给该 Agent |
| fan_out | 1 父 + N 子 | 子 Run 全部并发入队，父 Run 占位后即刻 completed |
| pipeline | 1 父 + N 子（`sequence=1..N`） | **只入队第一阶段**，每段完成后触发下一段；最后一段完成时父 Run 收敛为 completed（`result.stages` 记录各段状态与成本）；任一段失败则取消剩余段并把父 Run 标 failed |
| router | 1（父 Run 自执行） | 运行时用模型从组内选一个成员，再按该成员配置执行；决策写入 `result.routing`（`selected_agent_id/name`、`reason`、路由成本），路由调用成本计入该 Run |

> pipeline 各段输入 = 原始需求 + 上一段的 `result.answer`；父 Run 不会被单独入队（避免像 fan_out 那样立刻结束）。

## 运行

### GET /runs/{id}
详情 → 200 Run（含 steps 列表摘要、cost、result、error、artifacts）。

**Run**:
```json
{"id": "...", "request_id": "...", "assignee_type": "agent", "assignee_id": "...",
 "strategy": "direct", "status": "running", "current_step": 3,
 "result": null, "error": null, "cost_cents": 123, "started_at": "...", "finished_at": null,
 "artifacts": [{"id","filename","size_bytes","mime_type"}]}
```

### POST /runs/{id}/cancel
取消（FR-018）。→ 200 `{"status": "cancelled"}`。409 终态不可取消。

### POST /runs/{id}/retry
重试（FR-017，仅 failed/timeout）。→ 202 `{"status": "queued"}`。

### POST /runs/{id}/reply
回复补充信息（FR-021，needs_input 时）。请求 `{"message": "..."}` → 200 `{"status": "running"}`。

### GET /runs/{id}/artifacts/{artifact_id}
下载制品（FR-016）→ 302 到预签名 URL。

`?redirect=false` → 200 `{"url": "...", "filename": "...", "size_bytes": N}`。
浏览器里的 `<a href>` 无法携带 `Authorization` 头，直连本端点只会 401；前端先用
带鉴权的请求换取签名 URL 再跳转，对象存储始终只看到签名。
附件下载端点 `GET /requests/{id}/attachments/{key}` 提供同样的 `redirect` 参数。

## WebSocket

### WS /ws/runs/{id}
实时订阅运行事件（FR-015）。握手需 `?token=<access>`。

**服务端推送事件**:
```json
{"type": "status_change", "status": "running", "ts": "..."}
{"type": "step_new", "step": {"seq": 1, "kind": "llm", "status": "running", "ts": "..."}}
{"type": "step_update", "step": {"seq": 1, "kind": "llm", "status": "completed", "output": {...}, "cost_cents": 5, "duration_ms": 1200}}
{"type": "log_line", "line": "正在调用工具 web_search...", "level": "info", "ts": "..."}
{"type": "cost_update", "cost_cents": 123}
{"type": "run_completed", "result": {...}}
{"type": "run_failed", "error": "..."}
{"type": "run_cancelled"}
{"type": "needs_input", "message": "需要补充信息: ..."}
```

**客户端消息**（可选）: `{"type": "ping"}` → 服务端 `{"type": "pong"}`（保活）。

## 审计与统计

### GET /stats/overview
仪表盘 → 200（字段与前端 `StatsOverview` 一致）：
```json
{"running": 3, "queued": 2, "completed_today": 12, "failed_today": 1,
 "success_rate_7d": 0.92, "cost_today_cents": 345, "cost_trend_7d": [0, 100, 0, 0, 40, 0, 345]}
```
- `running` / `queued`：当前非终态计数，**不受 7 天窗口限制**（跑了很久的任务也必须可见）
- `cost_trend_7d`：长度 7 的每日成本数组（索引 0 最早、6 为今日），无日期标签，前端按位置渲染

### GET /audit-logs
管理后台审计（admin）。→ 200 `{"items": [AuditLog], "total": n}`。参数 `?user_id=&action=&page=`。

## 错误码约定

| HTTP | code | 场景 |
|---|---|---|
| 400 | INVALID_INPUT | 参数校验失败 |
| 401 | UNAUTHORIZED | 未认证/token 失效 |
| 403 | FORBIDDEN | 角色不足（viewer 写）/ 参与者缺字段权限 |
| 404 | NOT_FOUND | 资源不存在 **或非相关人**（016 起统一，防存在性枚举） |
| 409 | CONFLICT | 状态冲突（已在执行/终态/邮箱重复/组引用） |
| 422 | VALIDATION_ERROR | schema 校验失败 |
| 429 | RATE_LIMITED | 配额/频率限制 |
| 500 | INTERNAL_ERROR | 未预期异常（统一 JSON 兜底，详情只进日志不回传） |
