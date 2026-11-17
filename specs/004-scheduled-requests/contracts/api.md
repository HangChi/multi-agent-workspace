# Contracts: 定时调度（按规则自动建单并分配执行）

**Feature**: 004-scheduled-requests
**Base path**: `/api/v1`

三份关注点分离的契约：**规则管理**（人调用）、**触发历史**（人查询）、
**触发器处理**（系统内部调用，但它的判定顺序与副作用是必须钉死的契约）。

---

## 1. 规则管理 `/api/v1/schedules`

### 权限矩阵

| 端点 | viewer | member | admin |
|---|---|---|---|
| `GET /schedules` | 403 | 200（仅自己） | 200（全部） |
| `POST /schedules` | 403 | 201 | 201 |
| `GET /schedules/{id}` | 403 | 200（仅自己） | 200（任意） |
| `PATCH /schedules/{id}` | 403 | 200（仅自己） | 403（不可改他人） |
| `DELETE /schedules/{id}` | 403 | 204（仅自己） | 403（不可删他人） |
| `GET /schedules/{id}/triggers` | 403 | 200（仅自己） | 200（任意） |

- **admin 可读不可写**：这是 FR-005 的刻意取舍。admin 的职责是排障（看谁挂了），
  不是替用户调度生产任务——若 admin 能改他人调度，改坏后责任与成本都落在创建者身上。
- **越权与不存在同响应**：`GET/PATCH/DELETE /schedules/{unknown}` 与
  `GET/PATCH/DELETE /schedules/{他人id}` 一律 **404 `NOT_FOUND`**，
  措辞统一为「调度不存在」。禁止用 403 区分"存在但无权"，否则可枚举他人调度（FR-006）。
  唯一的例外是**已知存在但角色不足**（viewer 对任何调度），此时 403 是安全的，
  因为 viewer 对所有调度都无权，403 不携带额外信息。

### POST /schedules

请求：

```json
{
  "name": "每日晨报",
  "description": "给团队汇总昨日进度",
  "cron_expr": "0 9 * * 1-5",
  "timezone": "Asia/Shanghai",
  "template": {
    "title": "每日进度汇总",
    "description": "汇总昨日所有完成与阻塞的需求",
    "expected_output": "markdown 摘要",
    "priority": "normal"
  },
  "target": { "type": "agent", "id": "<uuid>", "strategy": null },
  "enabled": true
}
```

响应 `201`：完整 `ScheduleOut`，含 `next_run_at`。

**校验顺序**（顺序是契约，决定用户先看到哪个错误）：

| # | 校验 | 失败响应 |
|---|---|---|
| 1 | 角色（member/admin） | 403 `FORBIDDEN` |
| 2 | 字段形状（长度、非空白、priority 枚举） | 422（Pydantic） |
| 3 | `cron_expr` 可解析且能算出下一时刻 | 422 `INVALID_INPUT`，`detail.field="cron_expr"` |
| 4 | `timezone` 是已知时区 | 422 `INVALID_INPUT`，`detail.field="timezone"` |
| 5 | 目标可用性（存在 + 属于调用者 + 未归档） | 404 `NOT_FOUND`「Agent 或组不存在」 |
| 6 | `strategy` 与目标类型相容（组策略不可用于 agent） | 400 `INVALID_INPUT` |

第 3 步的判据必须是**「能算出下一时刻」**，而不只是「能解析」。实测
`0 0 31 2 *` 会抛 `ValueError: day_of_month '31' never occurs in month_of_year '2'`，
而 `0 9 1 * 1` 虽可解析但可能很久才有一次——两者都不该被拒绝，只有**永远算不出**才拒绝。
实现上：解析后以当前时刻求一次 `next`，求不出来即 422。

### PATCH /schedules/{id}

- 部分更新，`null` 表示「不改」而不是「清空」（`template.description` 用空字符串清空）。
- 修改 `cron_expr` / `timezone` / `enabled` 任一，**必须重算 `next_run_at`**。
- 修改 `template` 或 `target` **不重算** `next_run_at`（时间语义未变）。
- 修改 `target` 立即做第 5 步校验。
- 冲突语义（US5 场景 2）：`PATCH` 与触发处理并发时，触发读取的是**某一完整版本**
  （行级 `SELECT ... FOR UPDATE` 保证读到的字段属于同一事务视图），
  不存在"目标是新的、模板是旧的"混合状态。

### DELETE /schedules/{id}

- `204`。删除规则级联删除触发记录，**已产生的需求与运行保留**
  （`requests.source_schedule_id` 置 `NULL`）。这是 data-model 中「删除语义」表的落地。
- 响应体为空，但必须写审计（`schedule_delete`）。

### 响应体 `ScheduleOut`

```json
{
  "id": "uuid",
  "owner_id": "uuid",
  "owner_email": "a@b.c",          // 仅 admin 列表与详情填充，member 看自己时为 null
  "name": "每日晨报",
  "description": null,
  "cron_expr": "0 9 * * 1-5",
  "timezone": "Asia/Shanghai",
  "template": { "title": "...", "description": null, "expected_output": null, "priority": "normal" },
  "target": { "type": "agent", "id": "uuid", "strategy": null, "name": "调研助手", "status": "active" },
  "enabled": true,
  "next_run_at": "2026-11-18T01:00:00Z",
  "last_run_at": "2026-11-17T01:00:03Z",
  "last_result": "fired",
  "consecutive_failures": 0,
  "auto_disabled_reason": null,
  "created_at": "...", "updated_at": "..."
}
```

`target.name` / `target.status` 是**展示用冗余**，每次读取实时取（不缓存），
用于让界面能显示「目标已归档」而不必再发一次请求。目标已删除时这两项为 `null`，
且 `target.status` 返回 `"missing"` —— 明确区分"归档"与"已删除"。

---

## 2. 触发历史 `/api/v1/schedules/{id}/triggers`

### GET（分页 + 过滤）

| 参数 | 类型 | 默认 | 约束 |
|---|---|---|---|
| `page` | int | 1 | ≥1 |
| `page_size` | int | 20 | 1..100 |
| `result` | enum nullable | 无 | `fired`/`missed`/`skipped_busy`/`failed` |

响应：`{"items": [TriggerOut], "total": n, "counts": {"fired": 12, "missed": 1, "skipped_busy": 3, "failed": 0}}`

`counts` 是该调度**全量**（非当前页、非当前过滤）的结果分布，用于列表头展示
「共 16 次：12 成功 / 1 错过 / 3 跳过」。一次 `GROUP BY` 查询算出。

`TriggerOut`：

```json
{
  "id": "uuid",
  "schedule_id": "uuid",
  "planned_at": "2026-11-17T01:00:00Z",
  "fired_at": "2026-11-17T01:00:03Z",
  "result": "fired",
  "reason": null,
  "missed_count": 0,
  "window_start": null,
  "window_end": null,
  "request_id": "uuid",
  "run_id": "uuid"
}
```

**排序**：`planned_at DESC, id DESC`。第二排序键不可省——同 `planned_at` 在理论上被唯一约束
禁止，但合并错过记录的 `planned_at` 取窗口起点，可能与另一条 `fired` 相同，
不加 `id` 会导致分页丢行/重行。

**`reason` 的措辞约束**：面向用户的中文短句，只允许以下取值集合（可带数量后缀）：

| result | reason 取值 |
|---|---|
| `fired` | `null` |
| `missed` | `服务未运行，错过 N 次` |
| `skipped_busy` | `上次运行尚未结束` |
| `failed` | `目标已归档` / `目标已删除` / `目标不可用` / `本月配额已用尽` / `创建者已停用` |

不得出现 UUID、表名、异常类名或堆栈（FR-016 要求"可读原因"）。

---

## 3. 触发器处理（内部契约，`backend/app/services/schedule_service.py`）

> 这一节没有 HTTP 端点，但它是本特性最需要钉死的契约：判定顺序、副作用、
> 幂等语义都在这里。测试直接针对它写，不通过接口曲线救国。

### 入口

```python
async def run_due_schedules(db: AsyncSession, *, now: datetime | None = None) -> int
```

由 Beat 每分钟调用的 Celery 任务包装。`now` 可注入（测试与时钟异常恢复用）。

### 单个规则的处理：`fire_schedule(db, schedule, *, now) -> ScheduleTrigger`

**判定顺序（契约，不得调换）**：

```
1. schedule.enabled is False                → 直接 return None（不写记录、不推进）
2. planned = schedule.next_run_at
   planned is None                          → return None（不写记录；由 CHECK 约束保证不该发生）
3. lag = now - planned
   lag > MISSED_GRACE(90s)                  → missed：算次数、写合并记录、
                                              next_run_at = next_after(now)、return
4. 前次需求（schedule 最近一条 fired 记录的 request_id）状态非终态
                                            → skipped_busy：写记录、
                                              next_run_at = next_after(planned)、return
5. 目标校验失败（不存在/归档/owner 不匹配/owner 已停用）
                                            → failed：写记录、consecutive_failures += 1、
                                              next_run_at = next_after(planned)、
                                              达到阈值则停用、return
6. 配额校验失败                              → failed（配额原因）：同上但**不重试**
7. 其余                                      → fired：建单 + 分配、写记录、清空失败计数、
                                              next_run_at = next_after(planned)、return
```

**为什么第 3 步在 4、5 之前**：停机 2 小时恢复后，若先判"前次未结束"，
这条规则会被记成 `skipped_busy`，用户看到的是"被跳过"而不是"错过了 24 次"，
真正的运维事实被掩盖。（已在 data-model 的判定顺序表中说明）

### 幂等：唯一约束先占位

```
BEGIN
  INSERT INTO schedule_triggers (schedule_id, planned_at, ...)   -- 先占位
     ON CONFLICT (schedule_id, planned_at) DO NOTHING
  若未插入（冲突）→ 说明该计划时刻已处理 → ROLLBACK，return
  继续后续步骤（建单/跳过/失败）
  UPDATE schedules SET next_run_at = ..., last_* = ...
COMMIT
```

**先占位再干活**，而不是先干活再写记录。原因：若先建单、再写触发记录时进程崩溃，
重启后会认为"这次没处理过"而重复建单——需求已经存在但记录不存在，唯一约束拦不住。
先占位则相反：崩溃只会留下"占位了但没建单"，表现为这次触发丢失（可接受，且符合
"宁可漏跑也不重复跑"的总体取舍）。

`ON CONFLICT DO NOTHING` 而非捕获异常，是为了避免在长事务里制造保存点。

### 建单 + 分配（复用 `assign()`）

```python
request = Request(owner_id=schedule.owner_id, title=..., ...,
                  source_schedule_id=schedule.id)
db.add(request)
await db.flush()          # 取 id，assign() 要求行已存在
await assignment_service.assign(
    db, request_id=request.id, user_id=schedule.owner_id,
    assignee_type=schedule.target_type, assignee_id=schedule.target_id,
    strategy=schedule.target_strategy,
)                          # 内部 commit：需求与 Run 一起提交
```

- `assign()` 抛 `AppError` → 整个事务回滚（占位记录也回滚），改记为 `failed` 并**重新
  用一个新事务**写触发记录（否则回滚会把记录一起抹掉）。实现上：
  捕获异常 → `rollback` → 开新事务写 `failed` 记录 + 推进 `next_run_at`。
- **必须验证**：`assign()` 失败后 `requests` 表里不能留下该需求（FR-009）。

### 与既有状态机的边界

- 调度**不自己**把需求置为完成/失败。需求状态由既有
  `request_service.settle_request_status` 在 Run 终态时收口。调度只在 `fired` 时把
  需求交给 `assign()`，后者按既有逻辑置 `running`。
- 调度**不**取消、重试、修改任何 Run。

### 必备行为用例表

测试必须覆盖下表每一行（左列为输入，右列为必须观察到的结果）：

| 输入情形 | 必须观察到 |
|---|---|
| 到点、目标正常 | 1 个 trigger(`fired`) + 1 个 request(status=running) + Run 结构与手工分配一致 |
| 到点、目标正常、`next_run_at` 未推进 | 第 2 次处理同 `planned_at` → 唯一约束冲突 → 不新增 request |
| 到点、agent 目标 | Run.assignee_type=`agent`、strategy=`direct`（未指定时） |
| 到点、组 + `fan_out` | 父 Run + N 子 Run，`parent_run_id` 正确，与手工分配逐字段比对 |
| 到点、组 + `pipeline` | 只入队 `sequence=1` 的子 Run（与手工一致） |
| 到点、组 + `router` | 单个父 Run，`assignee_type='group'` |
| 前次需求 `running` | trigger(`skipped_busy`)，reason=`上次运行尚未结束`，无新 request |
| 前次需求 `completed` | 正常 `fired` |
| 停机 2 小时（每 5 分钟，01:00 计划、03:00 恢复） | 0 个新 request，**1 条** `missed` 记录，`missed_count=25`（实测：01:00…03:00 共 25 个计划时刻全部被跳过，`next_run_at` 推进到 03:05） |
| 停机 9 分钟（每 5 分钟） | 1 条 `missed`，`missed_count=2`（实测：跨过 1:00 与 1:05 两个时刻） |
| 延迟 45 秒（< 宽限期） | `fired`，**不是** `missed` |
| 目标已归档 | `failed`，reason=`目标已归档`，无新 request，`consecutive_failures` +1 |
| 目标已删除 | `failed`，reason=`目标已删除`，无新 request |
| 目标属于他人 | `failed`，reason=`目标不可用`（**不泄露"存在但无权"**） |
| 配额已用尽 | `failed`，reason=`本月配额已用尽`，无 request，**不重试** |
| 创建者已停用 | `failed`，reason=`创建者已停用`，无 request |
| 连续失败达阈值 | 第 5 次后 `enabled=false`、`auto_disabled_reason` 非空、之后不再产生记录 |
| 停用状态下到点 | **无任何记录**（不是 `skipped_busy`，不是 `missed`） |
| 重新启用 | `next_run_at` 从启用时刻重算，不早于 now |
| `assign()` 内部失败 | 无孤儿 request、1 条 `failed` 记录、`next_run_at` 已推进 |
| 20 条规则同时到期 | 20 条记录全部写入、无遗漏；单条异常不影响其他 19 条 |
| 规则 A 抛异常 | 规则 B–T 仍正常处理（隔离） |

---

## 4. 与既有契约的交叉点

| 既有契约 | 本特性的约束 |
|---|---|
| `POST /requests`（`RequestCreate`） | 模板字段是本契约的子集；**不扩展** `RequestCreate` |
| `POST /requests/{id}/assign` | 调度**复用服务函数**，不经过 HTTP 层（避免二次鉴权绕路） |
| `GET /requests/{id}` | 响应**新增** `source_schedule_id` 与 `source_schedule_name`（可空） |
| `GET /runs/{id}` | **不改**。来源信息通过 request 关联获取 |
| `audit_logs.action` | 新增：`schedule_create`/`schedule_update`/`schedule_delete`/`schedule_enable`/`schedule_disable`/`schedule_trigger` |
| `settle_request_status` | **不改**。调度产生的需求由它收口 |

`GET /requests/{id}` 的增量字段必须可空且默认 `null`，否则会破坏既有前端与既有测试
（001 的契约测试对响应字段有断言）。这一点要在实现时用既有测试套件回归确认。
