# Data Model: 008 项目实体与需求归属

本特性只新增一张表、给 `requests` 加两列、加一个索引。**不做**的变更显式列在末尾。

## 1. `projects`（新表，迁移 0012）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | `Uuid` | PK，默认 `new_uuid()` | |
| `owner_id` | `Uuid` | FK `users.id` `ON DELETE CASCADE`，NOT NULL | 项目归属人（单人模型，R-01） |
| `name` | `String(100)` | NOT NULL | 项目名；`UNIQUE(owner_id, name)`（R-07） |
| `description` | `Text` | NULL | 可选描述 |
| `status` | `String(20)` | NOT NULL，默认 `active` | `active` / `archived`（R-03） |
| `created_at` | `timestamptz` | `server_default now()` | |
| `updated_at` | `timestamptz` | `server_default now()`，`onupdate now()` | |

约束与索引：
- `UNIQUE(owner_id, name)`：并发同名第二人 409（PG 约束兜底，service 层先查给出友好错误）。
- `ix_projects_owner_status(owner_id, status)`：列表端点固定按 owner 过滤 + 状态筛选。
- 删用户 → 级联删其项目 → 项目下需求 `project_id` SET NULL（见下），**不删需求**。

## 2. `requests` 加两列（迁移 0012）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `project_id` | `Uuid` | FK `projects.id` `ON DELETE SET NULL`，NULL | 业务归属；未归属为 NULL |
| `project_name` | `String(100)` | NULL | 展示快照，随项目改名同步（R-04） |

索引：
- `ix_requests_project_id(project_id)`：`GET /requests?project_id=` 过滤；
  与既有 `requests(owner_id, created_at DESC)` 等索引并存，不过改变有查询计划。

行为注记：
- `project_id` 变更仅限创建者（R-05，FR-005）——service 层按「本次 PATCH 是否
  含 project_id 键」分流权限，其他字段不动谓词。
- 项目删除 → 本列置 NULL；`project_name` 由 service 在同一事务显式清空
  （外键 SET NULL 只动 `project_id`，不动快照列——不清会留下「孤儿名字」）。
- 项目归档 → 两列都不动（R-03）。
- 创建/编辑需求时 `project_id` 取值校验：存在 + 属本人 + `status='active'`，
  否则 404（不存在/他人）或 400（已归档）。

## 3. `GET /projects` 的需求计数（FR-010）

响应带 `request_count`：该项目下、**当前调用人可见范围内**
（`owner_id = me OR assignee_id = me`，即 scope=relevant 口径）的需求数。
实现为一次 `SELECT project_id, count(*) FROM requests WHERE (owner_id=me OR assignee_id=me) AND project_id IN (...) GROUP BY project_id`，
与列表同一次往返——不物化计数表（删除/新建都会让物化值漂移，
20 个项目实时聚合的成本可忽略）。

## 4. 审计（FR-009）

`write_audit` 新增三个 action 常量（并入 `app/core/audit.py` 的集中定义区）：
`project_create` / `project_update` / `project_delete`；
需求 `project_id` 的变更复用既有 `request_update` action，
detail 带 `{from_project, to_project}`（不新增动作类型——归属变更是需求编辑的一种）。

## 5. 明确不做

- ❌ `request_dependencies` 表 / DAG / 拓扑排序（R-08，留后续特性）。
- ❌ `project_members` 表 / 项目共享（R-01，P2 按组织形态再议）。
- ❌ 项目级统计/配额物化表（成本恒记 request.owner_id，007 纪律不动；
  要「项目花了多少」走 stats 按 project_id 聚合 runs）。
- ❌ `requests` 既有列的任何类型/默认值变更（SC-006 零回归）。
- ❌ 软删除（`deleted_at`）——项目删除即物理删除 + 需求 SET NULL，
  与 agents/groups/schedules 的既有删除语义一致（它们是 status=archived
  或物理删，本特性的归档已覆盖「保留」场景）。
