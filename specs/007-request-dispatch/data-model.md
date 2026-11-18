# Data Model: 007-request-dispatch

> 只写本特性**新增或变更**的数据结构；「不做哪些变更」见 §6。
> 迁移注意事项沿用 006 在 PostgreSQL 16 上的实测经验（见 §5）。

## 1. `requests` 表变更

| 变更 | 列 | 类型 | 约束 |
|---|---|---|---|
| 新增 | `assignee_id` | `uuid NULL` | `FK → users.id ON DELETE SET NULL`，索引 |
| 新增 | `dispatched_at` | `timestamptz NULL` | 最近一次分派/转交时间 |
| 新增 | `accepted_at` | `timestamptz NULL` | 当前接收人接收时间；退回时清空 |
| 变更 | `status` 枚举 | `request_status` + `pending` / `accepted` | 见 §2 |

**语义约定**：

- `assignee_id IS NOT NULL` ⇔ 状态 ∈ {`pending`, `accepted`}（不变量，由状态机迁移保证）；
- `accepted_at` 非空 ⇔ 状态 = `accepted`；
- 退回（`created`）与终态不清空 `assignee_id` 以外的任何东西——分派历史在
  `request_dispatches` 表追溯，本表只存「当前责任指针」。

**`ON DELETE SET NULL` 的理由**：用户被删除时，其收件箱里的需求不应连带消失
（需求是「已经发生过的事」，同 004 `source_schedule_id` 的纪律）。删除用户 →
需求失去当前经手人，回到 owner 可见范围，历史表保留记录。

**新索引**：`ix_requests_assignee_status ON requests (assignee_id, status)` ——
收件箱查询（「分派给我且 pending/accepted」）与未读相关的热点路径。

## 2. 状态机（Request）

```
created ──dispatch──▶ pending ──accept──▶ accepted ──assign(agent/group)──▶ assigned
   ▲                    │                  │                                │
   │                    │ reject           │ reject                         │
   └────────────────────┘                  └────────────────────────────────┘
                            （assigned → running 由既有 assign 链路完成；
                              running 及以后状态不接受任何分派类操作 → 409）
```

迁移表（集中实现在 `app/orchestrator/request_state_machine.py`，对齐 Run 状态机样式）：

| from | 允许 to | 触发 |
|---|---|---|
| `created` | `pending` | dispatch（拖到员工/选人） |
| `pending` | `accepted` | accept（仅当前 assignee） |
| `pending` | `pending` | reassign（换接收人；幂等语义=转交） |
| `accepted` | `pending` | reassign（转交给同事） |
| `accepted` | `assigned` | assign to agent/group（既有链路，零行为变更） |
| `pending` / `accepted` | `created` | reject（清 assignee 三列） |
| `created` | `assigned` | assign to agent/group（既有：自派自处理路径） |
| 其余（含 `assigned`/`running`/终态） | — | 一律 `TransitionError` → 409 |

不变量校验（每次迁移后断言，测试锁定）：

1. `assignee_id IS NOT NULL` ⇔ status ∈ {`pending`, `accepted`}；
2. `accepted_at IS NOT NULL` ⇔ status = `accepted`；
3. `assigned` 及以后 `assignee_id` 保留不清（「这份活最终落在谁头上」可查），
   但不再接受分派类操作。

## 3. 新表 `request_dispatches`（append-only）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | `uuid PK` | |
| `request_id` | `uuid FK → requests.id ON DELETE CASCADE` | 索引 |
| `action` | `enum(dispatch/reassign/accept/reject)` | `dispatch`=首次或清空后再分派；`reassign`=更换接收人 |
| `actor_id` | `uuid FK → users.id` | 操作人 |
| `from_assignee_id` | `uuid NULL FK → users.id` | 原接收人（dispatch 为 NULL） |
| `to_assignee_id` | `uuid NULL FK → users.id` | 新接收人（accept 为当前人、reject 为 NULL） |
| `reason` | `text NULL` | reject 必填，其余可空 |
| `seq` | `int NOT NULL` | 每需求单调序号（行锁下 max+1）。排序键**不能用 created_at**：PG 的 `now()` 是事务开始时间，行锁等待会让后插入的事务持有更早时间戳，并发下经手链会说反「谁先谁后」（实测翻转，见 007 Phase 3b 提交记录） |
| `created_at` | `timestamptz` | `server_default now()` |

索引：`ix_request_dispatches_request_seq (request_id, seq)`（时间线查询）、
`ix_request_dispatches_request_id`（按需求过滤）。

**只插入不更新不删除**（宪法不可变纪律，同 004 调度触发历史）。查询面：
按 `request_id` 查经手链（US1/US2 详情页时间线）；按 `actor_id`/`to_assignee_id`
反查「我经手/派给我的需求」（本期不做，索引预留）。

## 4. 新表 `notifications`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | `uuid PK` | |
| `user_id` | `uuid FK → users.id ON DELETE CASCADE` | 索引 |
| `type` | `enum(request_dispatched/request_accepted/request_rejected/request_reassigned/run_completed)` | |
| `request_id` | `uuid NULL FK → requests.id ON DELETE CASCADE` | 运行完成类通知可空（防御性） |
| `title` | `varchar(200)` | 展示用一句话（含需求标题快照） |
| `payload` | `jsonb default '{}'` | `{request_title, actor_name, reason?}` 展示细节 |
| `read_at` | `timestamptz NULL` | NULL=未读 |
| `created_at` | `timestamptz` | `server_default now()` |

**索引**：`ix_notifications_user_unread ON notifications (user_id, created_at DESC) WHERE read_at IS NULL`
（部分索引：未读计数与列表的热点路径，已读行不入索引，控制体积）。

**注意**：`title` 存需求标题**快照**而非只存 `request_id`  JOIN——需求标题可改，
通知是「当时发生了什么」的记录（同 §3 不可变纪律）。

## 5. 迁移注意事项（PostgreSQL 16，沿用 006 实测）

1. **枚举值新增**：`ALTER TYPE request_status ADD VALUE IF NOT EXISTS 'pending'` /
   `'accepted'`。PG 12+ 允许在事务内执行 `ADD VALUE`，但**同一事务内不能使用新值**——
   alembic 迁移若在同一 migration 里 `ADD VALUE` 后又跑使用新枚举值的 DML 会报
   `unsafe use of new value`。处理：枚举 DDL 与数据回填（如有）分两个 migration，
   本特性无存量数据回填（新列全可空），单 migration 即可。
2. **downgrade**：删三列 + 两表；枚举值**不删**（PG 无安全删除途径，且旧代码可能
   仍引用，同 006 migration 0010 的处置）。
3. **外键 `ON DELETE` 选择**：`notifications.request_id` 用 CASCADE（通知随需求删除
   无存在意义）；`request_dispatches.request_id` 用 CASCADE（历史是需求的附属事实）；
   `requests.assignee_id` 用 SET NULL（需求本体优先于经手人存在）。
4. `notifications.user_id` CASCADE：用户删除时通知随之清除（个人收件箱数据，
   无跨用户审计价值；审计走 audit_logs）。

## 6. 明确不做（本特性不引入）

| 不做 | 留 | 理由 |
|---|---|---|
| `projects` 表 | 008 | 项目容器是独立领域概念（spec §2.2） |
| 需求依赖表（`request_dependencies`） | 008 | 与分派解耦 |
| 组织/部门表（`departments`） | 待组织维度引入 | 无数据来源，先不做权限矩阵 |
| `users` 表任何变更 | — | 三角色 RBAC 不动；人员选择是只读投影 |
| `runs` 表任何变更 | — | 执行引擎零改动（R-03 只改 quota 计算入参） |
| 通知已读物化计数 | — | R-10：COUNT 足够，物化多一个一致性维护点 |

## 7. ER 关系增量

```
users 1──N requests(owner_id)            （既有）
users 1──N requests(assignee_id)         （新增，可空）
requests 1──N request_dispatches         （新增，append-only）
users 1──N notifications                 （新增）
requests 1──N notifications(request_id)  （新增，可空）
```
