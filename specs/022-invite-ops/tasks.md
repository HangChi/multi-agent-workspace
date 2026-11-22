# Tasks: 邀请码运营（022）

## 规格
- [X] T001 spec.md（现状盘点 / 部门配额 / 统计口径 / **审批流未做与理由**）
- [X] T002 research.md（R-01~R-06：撤销不释放 / 月度窗口 / 整批校验 / 统计分离 /
      配额在部门 / 不做审批流）
- [X] T003 tasks.md

## 后端
- [X] T101 迁移 `0022_invite_ops`（departments.invite_quota + invite_codes.department_id + 索引）
- [X] T102 模型字段 + 索引声明
- [X] T103 `_assert_department_quota`（本月 SUM(max_uses) + 本次 ≤ quota；撤销仍占）
- [X] T104 单张/批量发码接受 department_id（批量按 count × max_uses 整批校验）
- [X] T105 `invite_stats`（总量 / 按角色 / 按部门本月与配额 / top5 码）
- [X] T106 `GET /admin/invite-codes/stats`（admin-only）
- [X] T107 列表与单张返回 department_id + department_name
- [X] T108 `DepartmentUpdate.invite_quota`（显式 null = 不限；不传 = 不改）
- [X] T109 测试 8 例（配额 409 / 整批不留半批 / 撤销仍占 / 无配额不限 /
      统计口径 / 非 admin 403 / UTC 月界 / PATCH 语义三态）
- [X] T110 门禁：ruff + format + mypy + pytest

## 前端
- [X] T201 `api/inviteCodes.ts`：department 字段 + `fetchInviteStats` + 类型
- [X] T202 邀请码页：运营概览（张数 / 已发 / 已用 + 按部门本月进度）
- [X] T203 发码表单加「归属部门」下拉（未归属不占部门预算）
- [X] T204 门禁：eslint + tsc + vitest

## 验收
- [X] T301 `s18_invite_ops.py` 3/3（配额 409 / 统计 / 非 admin 403）
- [X] T302 迁移 `0021 → 0022` 真实库执行
- [X] T303 文档与提交

## 实测记录（2026-11-21）

| 门禁 | 结果 |
|---|---|
| 后端 | **2091 passed**（基线 2084，新增 8；另有 1 例为 PATCH 语义补充），覆盖率 **94.18%** |
| 前端 | **154 passed**（无新增单测，页面由手工与 e2e 基线覆盖） |
| 真实栈 s18 | **3/3** |
| 迁移 | `0021 → 0022` 成功 |

## 未完成（本轮明确不做，**后已由 027 补做**）

- [X] **申请-审批流**（member 提交申请 → admin 审批 → 自动发码）。
      022 当年的理由是「正确形态是配合组织编制的 HC 管理，孤立做会得到一张没人填的表」。
      **027 用一个更小的闭环补上了**：申请 + 审批 + 自动发码，不引入 HC 概念
      （见 `specs/027-invite-approval/`，tasks 全 `[X]`）。
      批准直接复用 022 的 `create_invite_codes`，部门月度配额与「整批不留半批」
      自动继承；配额不足 → 409 且申请保持 pending，不出现「批了却没码」。
