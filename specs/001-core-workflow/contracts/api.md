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
编辑（归属校验，403 非 owner）。请求部分字段。→ 200 Agent。

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
- 409 需求已在执行（FR-012）；404 目标不存在/无权限；400 目标已归档。

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
| 403 | FORBIDDEN | 无权限/非资源 owner |
| 404 | NOT_FOUND | 资源不存在 |
| 409 | CONFLICT | 状态冲突（已在执行/终态/邮箱重复/组引用） |
| 422 | VALIDATION_ERROR | schema 校验失败 |
| 429 | RATE_LIMITED | 配额/频率限制 |
| 500 | INTERNAL_ERROR | 未预期异常（统一 JSON 兜底，详情只进日志不回传） |
