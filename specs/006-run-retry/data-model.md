# Feature 006 Data Model: 运行自动重试

> **Branch**: `feat/006-run-retry` | **Date**: 2026-11-18 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

## 1. 变更总览

本特性**不新建表**，只做三处变更：

1. `runs` 表新增 3 列（尝试计数、下次重试时刻、历次失败原因）
2. `run_status` 枚举新增 1 个值 `retrying`
3. 状态机迁移表扩展（代码层，见 §4）

## 2. `runs` 表新增列

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `attempt` | integer | NOT NULL DEFAULT 1 | 当前是第几次尝试。首次执行为 1，每次自动/手动重试 +1 |
| `next_retry_at` | timestamptz | NULL | 下次重试的计划时刻；非 NULL 表示正在等待重试。**状态冗余字段**，与 `status='retrying'` 同真同假，用于 reaper 排除与前端倒计时 |
| `retry_history` | jsonb | NOT NULL DEFAULT `'[]'::jsonb` | 历次失败记录，元素形如 `{"attempt": 1, "error": "...", "at": "2026-11-18T09:00:00+00:00"}` |

### 为什么不建 `run_attempts` 表

尝试次数有硬上限（默认 3，可配但不会到几十）。jsonb 的读取场景只有
「详情页展示全部失败原因」一种，没有按 attempt 聚合查询、没有外键关联需求。
建表会带来 JOIN、孤儿行清理、以及「父 Run 删了尝试行怎么办」三个新问题，
换来的是一个用不上的查询能力。

### 为什么不复用 `runs.error`

`error` 是「当前（最后一次）失败原因」，重试成功后它必须被清掉——
而历史原因必须保留（spec FR-017）。两个生命周期不同，不能合并。

## 3. `run_status` 枚举

```
queued | running | needs_input | retrying | completed | failed | cancelled | timeout
```

`retrying` 是**中间态**：它会自动迁移到 `running`（到点重试）或 `failed`（次数耗尽），
不进 `_TERMINAL`。

迁移注意：PostgreSQL 的 `ALTER TYPE ... ADD VALUE` 不能在事务块内与同一类型的
后续使用混用（PG 11 前完全禁止事务内执行；PG 11+ 允许但同事务内不能用新值）。
因此迁移文件对该语句单独处理：Alembic 的 `op.execute` 默认在事务内，
需确认 PG 16 行为后决定是否 `with op.get_context().autocommit_block()`。
**验证点**：迁移在空库与既有数据库上都能跑（CI 的 `alembic upgrade head` 覆盖空库，
本地开发库覆盖有数据）。

## 4. 状态机（代码层）

```
                 ┌──────────────────────────────┐
                 │                              │
                 ▼                              │
queued ──► running ──► retrying ────────────────┘
              │  │         │
              │  │         └──► failed（次数耗尽）
              │  └──► needs_input ──► running / cancelled
              ├──► completed
              ├──► failed ──► queued（手动重试，attempt+1，成本不清零）
              ├──► cancelled
              └──► timeout
```

新增迁移：`running → retrying`、`retrying → running`、`retrying → failed`。
`retrying` **不可**直接到 `completed`（必须先 `running`）。

`failed → queued` 规则保留，但手动重试现在也递增 `attempt`：
达到上限后的手动重试是否允许？**允许**——手动重试是用户显式决定，
不应被自动重试的预算挡住（spec 未禁止，且 FR-006 的「上限」约束的是自动重试）。

## 5. 与其他表的关系

无变化。`retry_history` 是 jsonb，无外键；`attempt` / `next_retry_at` 是标量。
pipeline 的父子关联（`runs.parent_run_id`）不受影响：父 Run 的收敛规则只看
子 Run 的终态，`retrying` 不是终态，因此父 Run 会继续等待（既有行为一致）。

## 6. 索引

不需要新索引：

- `next_retry_at` 的查询场景是「reaper 排除重试中」，已有 `status` 维度可滤
- 没有「捞出所有到点待重试的 Run」这种查询——重试由 Celery 的延迟消息驱动，
  不轮询数据库（这是 research R-01 的直接结果）

## 7. 数据完整性不变量

1. `attempt >= 1`
2. `status = 'retrying'` ⟺ `next_retry_at IS NOT NULL`
3. `retry_history` 长度 ≤ `attempt - 1`（首次执行还没有失败记录）
4. `status IN ('completed','failed','cancelled','timeout')` 时 `next_retry_at IS NULL`
5. `cost_cents` 单调不减（跨重试累计，spec FR-013）

不变量 2、3、4 由应用层保证（状态机迁移时同步写入），
不变量 5 由「重试路径不触碰成本字段」保证。均以测试固化。
