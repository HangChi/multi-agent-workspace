# Feature Specification: 邀请码申请与审批（027，022 的范围外补做）

**Status**: Implemented（2026-11-21）

## 问题陈述

022 明确不做审批流，理由是「正确形态是配合组织编制的 HC 管理」。027 做的是一个
**更小的闭环**：申请 + 审批 + 自动发码，不引入 HC 概念。

现在的加人路径是：群里问管理员 → 管理员发码 → 贴回来。三步都在系统外，事后
无从查起「谁批的、批了几个、为什么批」。申请表把这三步留在系统内。

## 设计

- 表 `invite_requests`：申请人 / 部门 / 角色 / 数量 / 理由 / 状态 / 审批人 / 发码结果。
- **批准走 022 的 `create_invite_codes`**，不另造发码路径：部门月度配额、
  「整批校验不留半批」自动继承。配额不足 → 409，申请**保持 pending**
  （绝不出现「批了却没码」）。
- 待审批上限 3 条/人：防刷屏把队列淹了。
- 审批结果发站内通知（`invite_request_approved` / `rejected`），并加入可订阅事件。
- 前端一页两用：成员提交 + 看自己的；admin 额外看待审批队列（批准/拒绝）。

## 需求

- **FR-001** `POST /invite-requests`（member）：role/count/reason(+department_id)。
- **FR-002** 同一人待审批 ≤3，超出 409。
- **FR-003** `GET /invite-requests?status=`：admin 看全部（可按状态），成员只看自己的。
- **FR-004** `POST /{id}/approve`（admin）：发 count 张 max_uses=1 的码，
  存 `issued_codes`（换行分隔），通知申请人。
- **FR-005** 配额不足时批准失败且申请仍 pending。
- **FR-006** `POST /{id}/reject`（admin，可带理由）：置 rejected + 通知。
- **FR-007** 已处理的申请再批/再拒 → 409。
- **FR-008** `notification_type` 枚举加两个值（**必须迁移**，017 的教训）。
- **FR-009** 两个新事件加入 `SUBSCRIBABLE_EVENTS` 与中文标签（018 的一致性测试要求）。

## 成功标准

- SC-001 提交 → pending；3 条后第 4 条 409。
- SC-002 批准 → approved + 2 个码，且**其中一个能真的注册成功**。
- SC-003 部门 quota=1 的 3 人申请 → 批准 409，申请仍 pending、无码。
- SC-004 member 批准 → 403；成员只能看到自己的申请。

## 实测（2026-11-21）

- 后端新增 5 例（提交 / 上限 3 / 批准发码+通知 / 配额门禁 / 拒绝与可见性），
  全量 **2123 passed / 1 failed → 修后全绿**（失败项正是 018 的事件白名单一致性，
  已把两个新事件加入 `SUBSCRIBABLE_EVENTS` 与标签）；
- 前端 155 passed（新页面 + 路由 + 导航）；
- 真实栈 `deploy/verify/s22_invite_requests.py` **4/4**（含「码真的能注册」）。

## 范围外

- 多级审批 / 审批人配置（一个公司一个 admin 足够）；
- 与 HC / 编制挂钩（那是 HC 管理特性的事）；
- 申请撤回（拒绝就等于撤回，加撤回只会多一个状态）。
