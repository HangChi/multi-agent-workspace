# Implementation Plan: 外部通知通道（018）

## Technical Context

- 后端：FastAPI + SQLAlchemy async；投递走 Celery（同步任务壳 + `asyncio.run`）
- 出网守卫：`app/core/net_guard.py`（017 起从 `read_url` 抽出，018 与 002 共用）
- 前端：React + TanStack Query；设置二级页 `/settings/notifications`
- 迁移：`0018_notification_channels`（两张新表，无枚举值）
- 依赖：零新增（httpx / hmac / hashlib 已在）

## Constitution Check

- [x] I. SDD + TDD：签名/守卫/投递分支都有对应测试（29 例后端 + 2 前端 + 2 e2e）
- [x] I. 未实测不勾：迁移、端到端投递、退订都在真实栈验过才勾
- [x] II. 范围边界：邮件/SMS、聚合摘要、频率限制、订阅表达式四项显式排除
- [x] III. 完整系统：配置 → 投递 → 记录 → 退订 → 前端管理，无半截
- [x] IV. 质量门禁：ruff / mypy / pytest（含覆盖率）/ eslint / tsc / vitest 全绿
- [x] 安全：SSRF 复用 002 判定；不跟随重定向；元数据地址任何开关都拦

## 实现策略

**核心在「一个 service + 一个 task」**：
`notification_channel_service.attempt_delivery` 做一次尝试并给出结局
（succeeded / failed / retry），`deliver_notification` 任务只按它说的退避重试。
业务侧只在 `create_notification` 里加一行「建记录 + 入队」。

### Phase 1 — 规格（本次）

spec / research / data-model / contracts / plan / checklists / tasks / quickstart。

### Phase 2 — 守卫抽取 + 后端

1. `core/net_guard.py`：`is_blocked_ip` / `resolve_hosts` / `resolve_public_hosts` /
   `normalize_http_url` / `is_metadata_address`；`read_url` 改为委托（保留 `FetchError` 语义）。
2. 模型 + 迁移 0018。
3. service：CRUD + `enqueue_for_notification` + `attempt_delivery` + `send_test`。
4. 任务 `deliver_notification`（`max_retries=2`，与 service 的 3 次尝试对齐）。
5. `create_notification` 挂钩（TESTING 短路、失败不阻断）。
6. API 端点 + 事件类型端点。

### Phase 3 — 前端

`api/notificationChannels.ts` + `NotificationChannelsPage`（列表 / 最近投递 /
新建弹层 / 测试 / 重置密钥 / 删除）+ 路由 + `SETTINGS_NAV` 入口。

### Phase 4 — 验收

`deploy/verify/s14_notification_webhook.py`（5 场景，含真实投递到 httpbin）；
e2e 两例（新建可见 / 私网被拒且消息可读）。

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| **SSRF**：用户填内网地址 | 保存时 400 + 投递前复查 + 不跟随重定向 + 元数据地址硬拦 |
| 用户 URL 挂掉导致队列积压 | 4xx 不重试；5xx 最多 3 次；`aw_celery_queue_depth` 告警兜底 |
| 事务内入队早于提交 | 任务查不到 delivery 时退避 5s 重来（最多 3 次） |
| broker 不可用导致站内通知失败 | 入队异常只 warning，站内通知照常（主通道优先） |
| 通道数量失控打满 worker | 暂不做频率限制，靠队列深度告警观察（research R-06） |
| 回滚 | `alembic downgrade 0017` 会丢投递历史（过程记录，可接受） |
