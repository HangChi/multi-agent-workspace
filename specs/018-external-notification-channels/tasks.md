# Tasks: 外部通知通道（018）

> 勾选纪律（宪法 I）：只有**实测通过**的才改 `[X]`。

## Phase 1 — 规格

- [X] T001 spec.md（5 个 US、FR-001~010、SC-001~005、Out of Scope + Webhook vs 邮件的取舍表）
- [X] T002 research.md（R-01~R-09：通道选型 / SSRF 与残余风险 / 不聚合 / at-least-once /
      4xx 不重试 / 不做限流 / 签名方案 / secret 存储 / 独立投递表）
- [X] T003 data-model.md（两张表、不变式、回滚面）
- [X] T004 contracts/api.md（8 个端点、错误矩阵、投递头契约与签名算法、重试表）
- [X] T005 plan.md（Constitution Check、四 Phase、风险表）
- [X] T006 checklists/requirements.md（含三条「判断被推翻」的 Notes）
- [X] T007 tasks.md（本文件）
- [X] T008 quickstart.md

## Phase 2 — 守卫抽取 + 后端

- [X] T101 抽 `core/net_guard.py`（blocked 网段 / is_blocked_ip / resolve_hosts /
      resolve_public_hosts / normalize_http_url / is_metadata_address）
- [X] T102 `executors/read_url.py` 改为委托 net_guard（保留 FetchError 语义），
      71 例 read_url 测试全绿
- [X] T103 模型 `NotificationChannel` / `NotificationDelivery` + `models/__init__` 注册
- [X] T104 迁移 `0018_notification_channels`（两表 + 索引 + CHECK）
- [X] T105 service：通道 CRUD（URL 校验、事件白名单、secret 生成/重置）
- [X] T106 service：`enqueue_for_notification`（订阅过滤 + 禁用不建）
- [X] T107 service：`attempt_delivery`（签名/头部/重试判定/4xx 终态/禁用丢弃）
- [X] T108 service：`send_test`（同步一次尝试）
- [X] T109 任务 `deliver_notification`（max_retries=2，退避由 service 给）
- [X] T110 `create_notification` 挂钩（TESTING 短路、失败只 warning）
- [X] T111 API 端点 8 个 + 事件类型中文标签
- [X] T112 单测 17 例（openssl 签名向量、事件白名单、URL 守卫、私网开关）
- [X] T113 集成测试 12 例（CRUD/权限/404、订阅过滤、200/500/4xx/放弃/禁用/测试）
- [X] T114 后端门禁：ruff + format + mypy + pytest（2053 passed / 94.62%）

## Phase 3 — 前端

- [X] T201 `api/notificationChannels.ts` + `endpoints.ts` re-export
- [X] T202 `NotificationChannelsPage`（列表 / 最近投递 / 新建弹层 / 测试 / 重置 / 删除）
- [X] T203 路由 `/settings/notifications` + `SETTINGS_NAV` 入口（memberOnly）
- [X] T204 组件测试 2 例（空态、事件语义文案 + 原样提交）
- [X] T205 前端门禁：eslint + tsc + vitest（150 passed / 28 files）

## Phase 4 — 验收

- [X] T301 `deploy/verify/s14_notification_webhook.py`（5 场景）真实栈 5/5
- [X] T302 e2e 两例（设置页新建可见、私网被拒且消息可读），全量 19 passed
- [X] T303 迁移在真实库执行（0017 → 0018），worker 注册新任务
- [X] T304 部署配置：compose 与 `.env.example` 加 `NOTIFICATION_ALLOW_PRIVATE_HOSTS`
- [X] T305 文档同步：README 已知限制、docs/README 特性表与数量（17 → 18）
- [X] T306 本文件与 checklists 回填
- [ ] T307 合并回 master 并删除特性分支

## 实测记录（2026-11-21）

| 门禁 | 结果 |
|---|---|
| 后端 pytest + 覆盖率 | **2053 passed**（基线 2024，新增 29），覆盖率 **94.62%** |
| ruff / format / mypy | 通过（365 文件格式化检查） |
| 前端 vitest | **150 passed / 28 files**（新增 2） |
| eslint / tsc | 通过 |
| e2e | **19 passed**（17 + 2），22.1s |
| 真实栈 s14 | **5/5**（真实投递 httpbin，succeeded/1 次，2.1s） |
| 迁移 | `0016 → 0017 → 0018` 全部在真实库执行成功 |

### 过程中被打回的三处判断

1. **`notifications.type` 是枚举**（017 的教训）——018 因此改用 varchar + CHECK。
2. **`except Exception` 包 `self.retry` 会吞掉 Celery 的 Retry**——只捕获
   `MaxRetriesExceededError`。
3. **fake DNS「一律公网」让私网测试假绿**——改为域名当公网、字面 IP 走真实判定。

### 未覆盖（如实记录）

- `deliver_notification` 任务壳覆盖率 42%：`TESTING=1` 时短路，CI 不跑 broker。
  重试逻辑本身由 service 的 6 个分支测试覆盖，任务是薄壳。
- 邮件通道未做（spec Out of Scope，需独立特性）。
