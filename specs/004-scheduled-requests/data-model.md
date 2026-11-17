# Data Model: 定时调度（按规则自动建单并分配执行）

**Feature**: 004-scheduled-requests
**Storage**: PostgreSQL 16 via async SQLAlchemy 2.0 / Alembic（迁移编号接在 `0006_artifact_run_step_index` 之后）

本文件只描述本特性**新增或写入**的数据结构。既有表（`requests`/`runs`/`steps`/`agents`/
`agent_groups`/`audit_logs`）**不做语义变更**，除 `requests` 增加一个可空的来源外键。

## Design rules

- 调度只**产生** `requests` 与 `runs`，不拥有它们。任何「这次执行怎么样」的问题都去查
  `runs`/`steps`，调度侧不复制执行结果。
- 触发记录是**事实表**，一旦写入不再修改（除合并错过记录写入时的次数）。
  幂等靠 `(schedule_id, planned_at)` 唯一约束，不靠进程状态。
- 规则修改只影响**未来**触发。已产生的需求、Run、触发记录都不会被回写。
- 时间一律 `timestamptz`，程序侧统一按 UTC 存储、按规则声明的时区求值。

## Entities

### Schedule

一条周期性触发规则。

| Field | Type | Rules |
|---|---|---|
| id | UUID | PK |
| owner_id | UUID | FK `users.id`，`ON DELETE CASCADE`；归属边界 |
| name | varchar(100) | required，非空白 |
| description | text nullable | 可选备注，仅展示 |
| cron_expr | varchar(120) | required；5 字段标准形式（分 时 日 月 周） |
| timezone | varchar(64) | required，默认 `Asia/Shanghai`；求值时区 |
| template_title | varchar(200) | required，非空白；触发时作为需求标题 |
| template_description | text nullable | 触发时作为需求描述 |
| template_expected_output | text nullable | 触发时作为期望产出 |
| template_priority | enum `request_priority` | `high`/`normal`/`low`，复用既有枚举类型 |
| target_type | enum `schedule_target_type` | `agent` / `group` |
| target_id | UUID | required；指向 `agents.id` 或 `agent_groups.id`（按 `target_type` 解释） |
| target_strategy | varchar(32) nullable | `direct`/`fan_out`/`pipeline`/`router`；为空时取既有默认 |
| enabled | boolean | required，默认 `true` |
| next_run_at | timestamptz nullable | 下次计划时刻；停用时为 `NULL` |
| last_run_at | timestamptz nullable | 最近一次**实际发生**的触发时刻（含跳过与失败） |
| last_result | enum `schedule_trigger_result` nullable | 最近一次结果，供列表页免联查展示 |
| consecutive_failures | integer | 默认 0；成功或跳过时归零，失败时 +1 |
| auto_disabled_reason | text nullable | 因失败阈值自动停用时写入可读原因 |
| created_at / updated_at | timestamptz | server default / on update |

**Constraints & indexes**

- `CHECK (cron_expr <> '')`、`CHECK (name <> '')`：空白规则无意义。
- `CHECK (not enabled or next_run_at is not null)`：启用态必须有下次触发时间，
  否则规则会静默永不触发。**这是防"看起来启用了但永不跑"的硬约束**。
- `CHECK (target_type in ('agent','group'))`：由枚举保证。
- 索引 `idx_schedules_due (next_run_at) WHERE enabled`：部分索引，只扫启用中的到期规则，
  扫描任务的主查询路径。
- 索引 `idx_schedules_owner (owner_id, created_at DESC)`：成员列表页。
- **不建** `(target_type, target_id)` 外键。原因：目标是多态的（agent 或 group），
  PostgreSQL 无法用一个外键表达；改用应用层校验 + 触发时重新校验（FR-023 要求每次都校验
  创建者当下的权限，因此本来就不能依赖数据库外键的静态保证）。代价是可能留下指向已删目标的
  规则——这正是 `consecutive_failures` 与自动停用要处理的情形。
- **不建** `(owner_id, name)` 唯一约束：同名规则在语义上无害，限制它只会让用户
  在复制规则时莫名撞墙。

### ScheduleTrigger

一次触发处理的可审计事实。

| Field | Type | Rules |
|---|---|---|
| id | UUID | PK |
| schedule_id | UUID | FK `schedules.id`，`ON DELETE CASCADE` |
| planned_at | timestamptz | required；**该次处理对应的计划时刻** |
| fired_at | timestamptz nullable | 实际处理时刻；错过记录为 `NULL` |
| result | enum `schedule_trigger_result` | `fired` / `missed` / `skipped_busy` / `failed` |
| reason | text nullable | 可读原因（中文，面向用户，不含内部标识符） |
| missed_count | integer | 默认 0；仅 `result='missed'` 时 > 0，表示该窗口内错过的计划次数 |
| window_start / window_end | timestamptz nullable | 仅 `missed`：被合并窗口的起止 |
| request_id | UUID nullable | FK `requests.id`，`ON DELETE SET NULL`；仅 `fired` 有 |
| run_id | UUID nullable | FK `runs.id`，`ON DELETE SET NULL`；父 Run，便于直达 |
| created_at | timestamptz | server default |

**Constraints & indexes**

- **`UNIQUE (schedule_id, planned_at)`** —— 本特性的幂等基石（R-03）。同一计划时刻
  第二次插入必然冲突，从而保证「同计划时刻最多建一张单」（FR-012）。
- 索引 `idx_schedule_triggers_lookup (schedule_id, planned_at DESC)`：历史列表主查询。
- 索引 `idx_schedule_triggers_result (schedule_id, result, planned_at DESC)`：按结果过滤。
- `ON DELETE CASCADE`（schedule → triggers）：删规则时历史随之消失。这与 FR-020 不冲突——
  FR-020 保护的是**需求与运行**不被改写，而不是"规则删了历史记录必须留着"。
  但**已产生的 `requests` 必须保留**（见下 §删除语义）。
- `request_id`/`run_id` 用 `SET NULL` 而非 `CASCADE`：需求被删（目前无此路径，但保留语义）
  不应连带删掉"曾触发过"的事实。

### Request（增量）

| Field | Type | Rules |
|---|---|---|
| source_schedule_id | UUID nullable | FK `schedules.id`，`ON DELETE SET NULL`；新增 |

- 新增字段**可空**，历史与手工创建的需求该列为 `NULL`，不需要数据回填。
- 用 `SET NULL` 而不是 `CASCADE`：删除规则后，它已产生的需求**必须继续存在**
  （US5 场景 3 / FR-020），只是失去反查入口。用 `CASCADE` 会在删规则时连带删需求，
  进而连带删 Run 与制品——这是不可接受的数据丢失。
- **不加** `planned_at` 到需求表：计划时刻属于触发事实，放在 `schedule_triggers`。
- **不改** 任何既有列的类型、默认值或语义。

## Enums

| Name | Values | 说明 |
|---|---|---|
| `schedule_target_type` | `agent`, `group` | 与既有 `run.assignee_type` 取值一致，便于映射 |
| `schedule_trigger_result` | `fired`, `missed`, `skipped_busy`, `failed` | 四种结果，与 FR-016 一一对应 |

两者都用 PostgreSQL 原生 enum（与项目既有 `request_status` 等一致），
在迁移的 `downgrade` 里显式 `DROP TYPE`（002/003 的迁移已建立该惯例）。

## State transitions

### Schedule.enabled

```
created(enabled=true, next_run_at=T)  ──disable──▶  enabled=false, next_run_at=NULL
                                      ◀──enable───  重新从"启用时刻"计算 next_run_at
```

- 启用时计算 `next_run_at` 的基准是**当前的 now**，而不是旧的 `next_run_at`。
  这直接实现 FR-007「不补偿停用期间的计划时刻」——停用 3 天后重新启用，
  下一次触发是"下一个符合表达式的时刻"，不是"3 天前的那个时刻"。
- 停用时 `next_run_at` 置 `NULL`，扫描任务的查询条件 `next_run_at <= now` 自然不会命中；
  同时 `CHECK` 约束保证不会出现"启用但没有下次时间"的死状态。

### ScheduleTrigger.result 的判定顺序

判定顺序本身是契约，必须固定，否则同一情形在不同实现里会落到不同分类：

1. **规则停用** → 不处理，**不写任何记录**（停用期间不存在这次触发，见 US5 场景 4）。
2. **`now - planned_at > 宽限期(90s)`** → `missed`：计算 `(planned_at, now]` 内应发生的次数，
   写一条合并记录（`missed_count`、`window_start=planned_at`、`window_end=now`），
   推进 `next_run_at` 到 `now` 之后的下一时刻，**不建单**。
3. **前一次触发的需求仍非终态** → `skipped_busy`：写记录、推进 `next_run_at`、**不建单**。
4. **目标不可用（不存在/已归档/无权限）** → `failed`：写记录、推进 `next_run_at`、
   `consecutive_failures += 1`，达到阈值则停用并写 `auto_disabled_reason`。
5. **配额/成本边界拒绝** → `failed`（原因区分"配额已用尽"）：同上，但**不重复重试**。
6. **其余** → `fired`：建单 + 分配，写记录并关联 `request_id`/`run_id`，
   `consecutive_failures = 0`，推进 `next_run_at`。

注意第 2 步与第 3 步的顺序：先判错过再判忙。若反过来，一条停机 2 小时后恢复的规则
会因为"上一次的需求还没结束"而被记成 `skipped_busy`，掩盖真正的错过事实。

## 删除语义（易错，单独说明）

| 操作 | `schedules` | `schedule_triggers` | `requests` | `runs` |
|---|---|---|---|---|
| 删除规则 | 删除 | 级联删除 | **保留**（`source_schedule_id` 置 NULL） | 保留 |
| 停用规则 | 保留（`enabled=false`） | 保留 | 保留 | 保留 |
| 删除创建者 | 级联删除 | 级联删除 | **保留**（`owner_id` 外键现状即如此） | 保留 |

「删除规则后需求必须保留」是本表设计的核心约束：用 `CASCADE` 会一路删到 Run 与制品。

## 迁移计划

单个迁移 `0007_schedules.py`：

1. `CREATE TYPE schedule_target_type / schedule_trigger_result`
2. `CREATE TABLE schedules`（含 CHECK 约束、部分索引）
3. `CREATE TABLE schedule_triggers`（含唯一约束 `(schedule_id, planned_at)`）
4. `ALTER TABLE requests ADD COLUMN source_schedule_id UUID` + 外键 + 索引

`downgrade` 逆序执行，并显式 `DROP TYPE`。**已验证过 002/003 的迁移在空库上
`upgrade → downgrade → upgrade` 往返**，本迁移须复现同一验证（见 quickstart）。

## 不做哪些变更（明确声明）

- **不**给 `runs` 加调度字段：Run 的来源通过 `requests.source_schedule_id` 反查，
  避免同一事实存在两处。
- **不**给 `requests` 加 `deadline`：规格已确认该字段在真实表结构中不存在，
  模板也不承诺截止时间。**若将来要加，属于独立特性**。
- **不**改 `requests.status` 枚举：调度产生的需求走同一套状态机，不得引入调度专属状态
  （FR-008 / US2 场景 4）。
- **不**改 `assign()` 的事务边界：调度复用它的 `commit`（R-05）。
- **不**新增 Cache/Queue 之外的存储设施：扫描状态全部在 PostgreSQL，不引入额外状态源。
