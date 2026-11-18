# Data Model: 010 项目共享与成员

本特性新增一张表、给 `GET /projects` 加派生字段、加两个通知类型、
扩展四处可见性口径。**不做**的变更显式列在末尾。

## 1. `project_members`（新表，迁移 0014）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `project_id` | `Uuid` | FK `projects.id` `ON DELETE CASCADE`，NOT NULL | |
| `user_id` | `Uuid` | FK `users.id` `ON DELETE CASCADE`，NOT NULL | 成员用户（在职） |
| `created_at` | `timestamptz` | `server_default now()` | 加入时间 |

约束与索引：
- `PRIMARY KEY (project_id, user_id)`：复合主键即唯一约束（成员关系无第二行）。
  同名并发加入由 PK 兜底转 409。
- **无 role 列**：成员身份即角色，owner 恒为 `projects.owner_id`（R-01）。
- `ix_project_members_user_id(user_id)`：「我是哪些项目的成员」反查
  （relevant 口径的热路径）。
- 删用户 → 成员行 CASCADE（其创建的需求 owner_id 不级联——需求是已发生的事）；
  删项目 → 成员行 CASCADE（US4：删项目不发通知，目标不存在）。

## 2. `GET /projects` 派生字段（FR-009）

- `member_count`：项目人数（**含 owner**——「这个项目有几个人」的自然口径）。
- `request_count`：语义升级为「该项目下按**调用人**可见口径的需求数」
  （owner ∪ assignee ∪ 成员，R-06）。成员视角 = 项目内全部。
  实现：现有 `_visible_request_counts` 的条件从
  `owner_id=me OR assignee_id=me` 扩展为再加一肢
  `project_id IN (我是成员的)`——一次查询，不物化。

## 3. 四处可见性口径的扩展（本特性的核心）

| 位置 | 现状（009 后） | 扩展为 |
|---|---|---|
| `GET /projects?scope=relevant` | 我创建的 ∪ 派给我需求所属的 | **+ 我是成员的** |
| `GET /requests?scope=relevant` | owner ∪ assignee | **+ 其项目的成员**（OR EXISTS 半连接） |
| `GET /projects/{id}` | owner only | owner ∪ 成员 |
| `GET /requests/{id}` | owner-or-assignee（403） | owner ∪ assignee ∪ 项目成员 |

写侧谓词同步扩展（FR-006）：`_check_owner` 的调用方（update_request/
assign/dependency 端点）从「owner-or-assignee」扩展为
「owner-or-assignee-or-project-member」；**`project_id` 字段单独收紧**为
「创建者或该项目 owner」（R-03）；accept/reject 不动（R-07）。

`scope=mine`（需求）与 `scope=mine`（项目）**不变**——「我创建的」语义
不含共享，回归测试据此保持。

## 4. 通知（FR-003）

复用 007 notifications 表，新增两个 type：
- `project_member_added`：加成员成功时发给**被加者**（不是广播），
  payload `{project_id, project_name}`。
- `project_member_removed`：移除成功时发给**被移出者**，payload 同上。
- **删项目不发**（R-05：点进通知 404，纯噪声）。

写入点：成员管理端点（POST/DELETE），同事务。

## 5. 审计（FR-010）

`project_member_add` / `project_member_remove`，
detail = `{"member_user_id": ..., "project_name": ...}`。

## 6. 成员管理的判定数据

- 添加目标校验：用户存在 + `status='active'`（停用 400，R-08）→ 复用
  `users` 表直接查，不新造端点。
- 「我是项目 owner」判定：`projects.owner_id == user_id`（已有）。
- 「我是项目成员」判定：`project_members` 半连接（新，§1 的索引）。

## 7. 明确不做

- ❌ role 列 / admin 第三态（R-01：两层是用户确认范围，第三态没数据）。
- ❌ 部门表 / 树形组织（用户范围外，FR-012）。
- ❌ 所有权移交 / 项目转让（独立特性，R-04 只做「禁止自移除」的守卫）。
- ❌ 成员申请/审批/邀请确认流（内部工具直接加，R-05 前置）。
- ❌ 停用用户级联移除成员（R-08）。
- ❌ accept/reject 谓词扩展（R-07：责任接收是个人动作）。
- ❌ `requests` 既有列的任何变更；`projects` 既有列的任何变更。
