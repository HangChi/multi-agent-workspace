# Data Model：项目所有权移交（017）

## 1. 结论先行：无 schema 变更

本特性**不新增表、不新增列、不新增索引、不新增迁移**。它只改变既有行的值。
`alembic` 停在 `0016_invite_code_revoked`（017 不产生 revision）。

## 2. 涉及的既有实体

### `projects`

| 列 | 本特性的用法 |
|---|---|
| `id` | 被移交的项目；加 `FOR UPDATE` 行锁 |
| `owner_id` | **本特性唯一被写的列**。由原 owner 改为新 owner |
| `name` | 参与同名冲突检查（与目标用户组队查 `uq_projects_owner_name`） |
| `status` | 只读：archived 不阻拦移交（009/008 的「归档只拦新建」纪律） |

### `project_members`

| 动作 | 条件 |
|---|---|
| `DELETE WHERE project_id=? AND user_id=new_owner_id` | 新 owner 若在成员表里，必须删除（010 R-01：owner 不双写，双写要靠同步维护，是纯冗余） |
| `INSERT (project_id, user_id=old_owner_id)` | 仅 `keep_as_member=true`；`created_at` 用默认 |

`ProjectMember` 无 `role` 列（010 明确），所以「原 owner 变成普通成员」不需要
任何额外字段——身份由「在不在 owner_id 上」决定。

### `notifications`

新增一个 `type`：`project_ownership_received`（表是字符串列，无枚举约束，
不需要迁移）。payload 结构：

```json
{"project_id": "uuid", "project_name": "官网改版", "previous_owner_id": "uuid"}
```

### `audit_logs`

新增一个 `action`：`project_transfer`（同样是字符串列）。
`detail` 结构：

```json
{"from_user_id": "uuid", "to_user_id": "uuid", "keep_as_member": true, "project_name": "官网改版"}
```

## 3. 不变式（实现后必须仍成立）

| 不变式 | 保障方式 |
|---|---|
| 每个项目恰好一个 owner | `projects.owner_id NOT NULL` + 单行更新 |
| owner 不出现在 `project_members` | 移交时删除新 owner 行 + 010 的 add_member 本来就拒绝把 owner 加成成员 |
| 同名项目在 owner 维度唯一 | `uq_projects_owner_name`（DB 层）+ 显式预检（应用层，给出可读 409） |
| 需求对项目的归属只是快照引用 | `requests.project_id` 不动；`project_name` 快照也不动（项目没改名） |

**特别注意**：移交**不改** `requests.project_name` 快照。只有改名才同步快照
（008 的做法）。若移交时顺手重写快照，会把「快照 = 改名时刻的名字」这个
语义搅浑，且完全是无效写入。

## 4. 权限口径（复用 010 的三级谓词，不新增）

```
participant(project, user) := project.owner_id == user.id
                             OR exists(project_members: project_id, user_id)
```

移交后：
- 新 owner：owner 谓词成立 → 四个 owner 动作全开；
- 原 owner（keep_as_member=true）：participant 成立 → 读与协作照旧，owner 动作 403；
- 原 owner（false）：participant 不成立 → 一律 404。

`GET /projects?scope=relevant` 的实现（`project_visible_to_user`）本来就按
participant 判定，**不需要改**；`scope=mine` 按 `owner_id` 过滤，也不需要改。
这是 010 把口径收敛到单一谓词的收益。

## 5. 排队/调度影响

无。项目不是执行实体，移交不触碰 `runs` / `schedules` / 配额口径
（配额恒记 `request.owner_id`，007 起如此）。

## 6. 回滚面

| 层面 | 回滚方式 |
|---|---|
| 代码 | 回退提交即可（无迁移需要 downgrade） |
| 数据 | **不会自动回滚**：已移交的项目仍指向新 owner。如需恢复，人工把 `owner_id` 改回并补/删成员行（用审计的 `project_transfer` detail 可查到 from/to） |
| 通知/审计 | 历史记录保留（审计表本就不可删） |
