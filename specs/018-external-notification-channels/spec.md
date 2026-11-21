# Feature Specification: 外部通知通道（018）

**Feature Branch**: `feat/018-external-notification-channels`

**Created**: 2026-11-21

**Status**: Draft

**Input**: 用户 2026-11-21「按 README 里的顺序逐个优化/实现」。007 把「邮件 / Webhook /
企业微信通知」列为本期不做并注明「需独立特性论证」——本特性就是这个论证 + 第一条通道。

## 问题陈述

站内通知（铃铛轮询 ≤30s）解决「打开系统能看到」，但解决不了两个真实场景：

1. **离开页面就漏消息。** 派给我的需求、跑完的运行、解锁的后续——都需要人**主动**
   回来看。内部协作工具的日常是「一天开一次」，而「B 等 A 跑完」是分钟级的事。
2. **团队已有的通知入口进不来。** 企业微信群机器人、飞书机器人、内部告警网关
   全都是一个「POST 一个 URL」的形态，缺这条出口，工作台就成了信息孤岛。

**为什么是 Webhook 而不是先做邮件**：

| | Webhook | 邮件 |
|---|---|---|
| 需要的基础设施 | 一个 URL | SMTP 服务器 / 第三方发信服务、域名 SPF/DKIM、退信处理 |
| 可达性 | 团队已用 IM 机器人，填 URL 即用 | 需要运维配好发信凭据，否则全体用户哑火 |
| 可测 | 本地起一个接收端就能端到端验 | 需要真实邮箱与投递链路 |
| 主要风险 | **SSRF**（用户填的 URL 由我们的服务器去请求） | 垃圾邮件/被标记、内容合规 |

邮件的风险面（发信合规、退信、域名信誉）远大于 Webhook，而 Webhook 的风险
已经在本项目里有成熟答案（002 的 `read_url` 出网防护）。所以先做 Webhook，
邮件作为独立特性另议（spec Out of Scope 明示）。

## User Scenarios & Testing *(mandatory)*

### US1 — 配一条 Webhook 通道（P1）

作为成员，我要在设置里填一个 URL、选要接收哪些事件、保存并启用。

- Given 我登录为 member；
- When `POST /notification-channels {kind: webhook, url, event_types}`；
- Then 通道落库并默认启用；列表里能看到它（URL 打码显示域名 + 路径尾部）。

### US2 — 事件发生时收到签名请求（P1）

作为接收端（企业微信机器人 / 内部网关），我要收到一个能验证来源的 POST。

- Given 通道已启用且订阅了 `request_dispatched`；
- When 有人把需求派给我（007 的 create_notification 触发）；
- Then 接收端在 10s 内收到 `POST <url>`，body 是 JSON（事件类型、标题、需求 id、
  发生时间），头部带 `X-AW-Signature`（`sha256=<HMAC(secret, timestamp.body)>`）与
  `X-AW-Timestamp`；接收端用 secret 复算能对上。

### US3 — 投递失败要看得见（P2）

作为配置者，如果我的 URL 挂了，我要知道——而不是「以为配好了却没收到过」。

- Given URL 返回 500 或超时；
- Then 重试 3 次（指数退避 2s/8s/30s），仍失败则记一条失败投递；
- 设置页显示「最近投递」：成功/失败、尝试次数、最后一次错误摘要。

### US4 — 退订是即时的（P2）

- When 我禁用或删除通道；
- Then 之后产生的事件**不再**投递（不排队、不补发）。

### US5 — 内网地址必须被拒（P1，安全底线）

- When 我填 `http://127.0.0.1:8000/hook` 或 `http://169.254.169.254/latest/meta-data`；
- Then **400**，消息说明「不允许内网/保留地址」；且**保存时**就拒，不是投递时才拒。

## Requirements *(mandatory)*

### Functional

- **FR-001** 提供通道 CRUD：`GET/POST /notification-channels`、
  `PATCH/DELETE /notification-channels/{id}`、`POST /notification-channels/{id}/test`。
- **FR-002** 通道字段：`kind`（本期仅 `webhook`）、`url`、`secret`（服务端生成，
  可重置）、`event_types`（子集，空 = 全部）、`enabled`。
- **FR-003** 保存时 MUST 校验 URL：仅 http/https、长度 ≤2048、
  **解析后任一 IP 落在内网/保留段即 400**（复用 002 的判定，不另写一套）。
- **FR-004** 通知创建时 MUST 为每个「启用 + 订阅了该事件类型」的通道创建投递记录；
  同一用户多条通道各投一份。
- **FR-005** 投递 MUST 异步（Celery），不阻塞业务事务；最多 3 次重试，
  退避 2s/8s/30s；`4xx`（除 429）不重试（对方明确拒绝），`5xx`/超时/连接失败重试。
- **FR-006** 每个请求 MUST 带 `X-AW-Timestamp` 与 `X-AW-Signature`：
  `sha256=HMAC_SHA256(secret, f"{timestamp}.{body}")`（防重放：接收端可校验时间窗）。
- **FR-007** 投递记录 MUST 保留：状态（pending/succeeded/failed）、尝试次数、
  最后错误摘要（≤500 字符）、最后尝试时间。
- **FR-008** 禁用/删除通道后 MUST 不再产生新投递；已排队的投递在执行前
  重查通道状态，禁用则丢弃。
- **FR-009** `POST /{id}/test` MUST 立即投递一条测试事件并返回结果（同步等一次尝试，
  不排队），供配置时确认弹道。
- **FR-010** 前端设置页 MUST 提供通道列表（含最近投递状态）、新建、启用/禁用、
  删除、「发送测试」与 secret 重置。

### Non-functional

- **NFR-001** 有迁移：两张新表（`notification_channels`、`notification_deliveries`），
  不改既有表。
- **NFR-002** 投递是 **at-least-once**：网络超时后我们无法确定对方是否收到，
  重试可能重复投递。接收端需按 `X-AW-Delivery`（投递 id）幂等。
- **NFR-003** 不新增运行时依赖：httpx + hmac + hashlib 已够。
- **NFR-004** secret 不落日志、不回显明文（列表只回「已配置」）。

## Key Entities

| 实体 | 说明 |
|---|---|
| `NotificationChannel` | `user_id` / `kind` / `url` / `secret` / `event_types`(JSON) / `enabled` / 时间戳 |
| `NotificationDelivery` | `channel_id` / `notification_id` / `status` / `attempts` / `last_error` / 时间戳 |

## Success Criteria

- **SC-001** 本地起一个 HTTP 接收端，配通道 → 触发一次派单 → 接收端 10s 内收到
  且 HMAC 校验通过（手工 + 脚本各验一次）。
- **SC-002** 接收端返回 500 时，投递记录显示 failed 且 attempts=3；
  返回 200 时 succeeded 且 attempts=1。
- **SC-003** `127.0.0.1` / `169.254.169.254` / `10.x` 在保存时被 400 拒绝。
- **SC-004** 禁用后 1 分钟内不再有新投递记录。
- **SC-005** 既有测试全绿（后端 2024 / 前端 148 / e2e 17）。

## Out of Scope

- **邮件 / SMS 通道**（需要 SMTP 或供应商凭据、发信合规与退信处理，独立特性）；
- 频道层面的聚合/摘要（每条通知一条投递，不做「每 5 分钟汇总」）；
- 投递频率限制与配额（内部工具；如被滥用再加，见 research R-06）；
- 事件类型细到「哪个项目的哪条需求」的订阅表达式（只按事件类型订阅）。
