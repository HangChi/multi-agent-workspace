# Feature Specification: 部门与分派范围（020）

**Status**: Implemented（2026-11-21）

**Input**: 用户 2026-11-21「A1–A6 逐个完成」中的 A4：部门 / 树形组织 + 分派权限矩阵。

## 问题陈述

007 的分派规则是「任何在职员工都能被指派」。公司一大就变成两个具体麻烦：

1. **隔壁部门把活派过来**：谁都能在选人下拉里看到全公司，然后拖给自己想甩的人；
2. **没有「我们部门」这个概念**：看板上分不清哪些活是哪个部门在承接，
   管理上只能靠项目名自己约定。

## 设计：树 + 三档范围，不做权限矩阵

| 概念 | 实现 |
|---|---|
| 组织 | `departments` 自引用树，物化路径 `path`（`/root/child/`） |
| 人员归属 | `users.department_id`（可空 = 未归属；删部门只清空归属） |
| 分派范围 | `departments.assign_scope`：`all`（全公司）/ `subtree`（本部门及下级）/ `self`（仅本部门） |

**为什么是「每层一个范围」而不是「谁能派给谁」的矩阵**：矩阵的复杂度在于组合
（N 个部门 × M 个目标 × 若干动作），而真实诉求只有一句「别让隔壁部门乱派」。
三档范围能表达这句话，且用户一看就懂；矩阵要培训。

**默认不收紧**：`assign_scope` 默认 `all`，且**没有部门的用户按 `all` 处理**。
升级后没有人的权限发生变化；管理员建树、设 `subtree`/`self`、把人归档之后才逐步生效。
这是唯一能被接受的引入方式——升级不该让任何人突然派不出活。

## 需求

- **FR-001** `departments` 表（id / name / parent_id / path / depth / assign_scope /
  时间戳），`UNIQUE(parent_id, name)`，`CHECK assign_scope IN ('all','subtree','self')`。
- **FR-002** `users.department_id`（FK，`ON DELETE SET NULL`）。
- **FR-003** 部门 CRUD：`GET /departments`（member 可读）、`POST/PATCH/DELETE`
  （admin）；不支持换上级（要挪就建新部门 + 挪人，避免静默搬子树）。
- **FR-004** `PUT /departments/users/{id}/department`（admin）设人员归属，null = 移出。
- **FR-005** 删部门：子树一并删除；其中成员 `department_id` 置空，账号与需求不受影响。
- **FR-006** 转交需求给员工时校验范围：`can_assign_to(actor, target)`；
  目标不在范围内 → **403**「你的部门无权把需求指派给该用户」。
  **派给自己永远允许**（否则连取消分派都没落点）。
- **FR-007** `GET /users/options` 按调用者的范围收窄——下拉里不出现选不中的人。
- **FR-008** 同一父下部门名唯一（409 可读消息）；不同父下允许同名。
- **FR-009** 前端 `/settings/organization`（adminOnly）：部门树（按 path 缩进）、
  新建（上级 + 范围）、改名、改范围、删除（二次确认说明子树与人员影响）、
  人员归属列表。

## 错误码

| 条件 | 状态 |
|---|---|
| 非 admin 建/改/删部门 | 403 |
| 同父重名 | 409（消息带部门名） |
| 上级部门不存在 | 404 |
| 部门不存在 | 404 |
| 分派目标超出组织范围 | 403 |
| `assign_scope` 非法值 | 422（schema Literal）/ 400（service 二次校验） |

## 成功标准

- SC-001 建 root → child 后 path/depth 正确，列表按 path 先序（树形渲染无需前端递归）。
- SC-002 `scope=self` 的人派给外部部门 → 403；派给自己 → 202。
- SC-003 未归属部门的人不受限（升级后行为零变化）。
- SC-004 删父部门后子树消失、成员变未归属。
- SC-005 受限用户的 `/users/options` 只含范围内的人 + 自己。

## 实测（2026-11-21）

- 后端新增 11 例（`tests/integration/test_departments.py`），全量 **2074 passed**，
  覆盖率 **94.41%**；
- 前端新增 2 例（`OrganizationPage.test.tsx`），全量 **154 passed / 30 files**；
- 真实栈 `deploy/verify/s16_departments.py` **4/4**；迁移 `0019 → 0020` 执行成功。

## 范围外

- 部门负责人 / 部门级自治（本部门管理员）；
- 按部门做配额或成本核算（成本口径仍是 `request.owner_id`，007 起的纪律）；
- 分派审批流、「跨部门派单需对方同意」；
- 部门层级的可见性隔离（需求可见性仍按「创建者 ∪ 接收人 ∪ 项目参与者」）。
