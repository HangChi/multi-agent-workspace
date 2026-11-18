# Data Model: 009 需求依赖编排

本特性新增一张表、给详情响应加派生字段、加一个通知类型。**不做**的变更显式列在末尾。

## 1. `request_dependencies`（新表，迁移 0013）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | `Uuid` | PK，默认 `new_uuid()` | |
| `request_id` | `Uuid` | FK `requests.id` `ON DELETE CASCADE`，NOT NULL | **被依赖方**（B：要等别人的那条） |
| `dependency_id` | `Uuid` | FK `requests.id` `ON DELETE CASCADE`，NOT NULL | **前置**（A：先做的那条） |
| `created_by` | `Uuid` | FK `users.id` `ON DELETE CASCADE`，NOT NULL | 连边人（审计溯源） |
| `created_at` | `timestamptz` | `server_default now()` | |

约束与索引：
- `UNIQUE(request_id, dependency_id)`：重复边没有第二语义（spec US1 场景 2）；
  并发重连由约束兜底转 409。
- `ix_request_dependencies_request_id(request_id)`：「B 依赖哪些」——门禁与
  详情概要的查询路径（assign 热路径，SC-003 的 RT 预算靠它）。
- `ix_request_dependencies_dependency_id(dependency_id)`：「谁依赖 A」——
  settle 解锁通知的反查路径。
- 显式加 `CHECK (request_id <> dependency_id)`：自依赖在库层就堵死
  （service 先查给 400，约束兜底——双 FK 同表时这是唯一能防呆的库级手段）。

命名纪律：`request_id` = 被依赖方（边的「主体」），与 `request_dispatches.request_id`
（主体需求）同构；`dependency_id` = 前置。**不要**叫 `blocker/blocked`——
「阻塞」是门禁视角的叙事，表里存的是中性的「先后」结构。

## 2. `GET /requests/{id}` 派生字段（FR-010）

响应体新增（详情端才有，列表端只有 `blocked`）：

```json
{
  "dependencies": [
    {"id": "edge-uuid", "request_id": "A-uuid", "title": "A 标题",
     "status": "running", "terminal": false}
  ],
  "blocked": true
}
```

- `dependencies` 按创建时间正序；`terminal` 由 status ∈ 终态集合算出。
- `blocked` = 任一前置非终态。**列表端**也返回 `blocked`（布尔，零 JOIN
  成本的做法见 §4），但不返回 `dependencies` 明细——看板一次 N 条，
  逐条查边是 N+1。

## 3. 通知（FR-007）

复用 007 `notifications` 表，新增 type `dependency_ready`：

- `user_id`：后续需求的 owner；若 assignee 非 owner，**两条**（同人去重）。
- `request_id`：后续需求（点通知跳转到它——「你可以启动的是 B」）。
- `payload`：`{"dependency_id": "...", "dependency_title": "...",
  "dependency_status": "completed|failed|cancelled"}`——前端按
  dependency_status 渲染三种措辞（spec US3 场景 2）。

写入点：`settle_request_status`（需求终态唯一汇聚点，research R-02），
同事务内先反查「谁依赖我刚收敛的这条需求」、再逐条判定「其余前置是否
都已终态」，满足才发。判定 SQL 一次取全（见 §4），不逐条查。

## 4. 列表端 `blocked` 的零 N+1 算法

`GET /requests` 返回 N 条需求时，`blocked` 用**一次半连接**算出：

```sql
SELECT r.id, (EXISTS (
  SELECT 1 FROM request_dependencies d JOIN requests dep ON dep.id = d.dependency_id
  WHERE d.request_id = r.id AND dep.status NOT IN ('completed','failed','cancelled')
)) AS blocked FROM requests r WHERE <既有过滤>
```

或更简单的两查询：先取本页 N 条的 id，再一次
`SELECT request_id FROM request_dependencies WHERE request_id IN (:ids)
 AND dependency_id IN (SELECT id FROM requests WHERE status NOT IN (...))`，
在内存里判 N 条的 blocked。两者等价；实现选后者（SQL 更直白，
IN 列表长度 = 页大小，有上界）。

## 5. 门禁查询（assign 热路径，SC-003）

```sql
SELECT dep.id, dep.title, dep.status
FROM request_dependencies d JOIN requests dep ON dep.id = d.dependency_id
WHERE d.request_id = :request_id AND dep.status NOT IN ('completed','failed','cancelled')
```

走 `ix_request_dependencies_request_id`；空结果 = 放行。**一次查询**，
是 assign 全链路新增的唯一成本（目标校验/配额原样）。

## 6. 审计（FR-011）

`request_dependency_add` / `request_dependency_remove`，
detail = `{"dependency_id": ..., "dependency_title": ...}`（标题快照——
事后排查「这条边当初连的是哪条需求」不依赖目标还存在）。

## 7. 明确不做

- ❌ `blocked` 状态 / 状态机新态（R-03：门禁是校验不是状态）。
- ❌ 拓扑排序、关键路径、层级深度、批量级联启动（用户范围外，FR-013）。
- ❌ 依赖历史表（append-only 边）——增删都走审计日志，边本身不更新。
- ❌ 边的软删除（`deleted_at`）——删边是即时语义（门禁立即解除），
  留软删行会让「唯一约束」和反查都要带 `deleted_at IS NULL`，纯负担。
- ❌ 对 `requests` 既有列的任何变更（SC-005 零回归）。
- ❌ 重试路径的依赖重查（R-08）。
