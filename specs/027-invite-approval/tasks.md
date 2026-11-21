# Tasks: 邀请码申请与审批（027）

- [X] T101 迁移 `0026_invite_requests`（表 + 3 条 CHECK + 2 个索引 + **枚举加值**）
- [X] T102 `InviteRequest` 模型 + 注册
- [X] T103 `invite_request_service`（create 上限 3 / approve 走 022 发码 / reject）
- [X] T104 端点 4 个（提交 / 列表 / 批准 / 拒绝）+ 审计
- [X] T105 schema（Create / Out / List / Reject）
- [X] T106 测试 5 例
- [X] T107 修 018 一致性：两个新事件加入 SUBSCRIBABLE_EVENTS 与中文标签
- [X] T108 前端 `InviteRequestsPage`（一页两用）+ 路由 + nav
- [X] T109 门禁：ruff / format / mypy / eslint / tsc / vitest
- [X] T110 真实栈 s22 4/4

## 实测记录（2026-11-21）

| 门禁 | 结果 |
|---|---|
| 后端 | **2123 passed**（新增 5），覆盖率 **94.16%**；另有一处 018 一致性失败已修 |
| 前端 | 155 passed（新页面未加单测，由真实栈覆盖） |
| 真实栈 s22 | **4/4**（含「批准的码真的能注册」） |
| 迁移 | `0025 → 0026` 成功（含 `notification_type` 加两个值） |

### 被 018 的一致性测试抓到

新加的两个通知类型没有登记到 `SUBSCRIBABLE_EVENTS`——`test_subscribable_events_cover_notification_enum`
立刻红。这正是 018 立这条测试的目的：**新增通知类型必须显式决定「是否可被外部订阅」**，
不能沉默地漏掉。已加入白名单与中文标签。
