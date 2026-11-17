# Quickstart: 004 定时调度

本指南验证「规则 → 触发 → 建单分配 → 历史追溯」的完整闭环，以及本特性最容易做错的四处：
**幂等**、**错过不补跑**、**判定顺序**、**变更不污染历史**。

> 状态：**尚未开始实现**。本文所有「预期」都是待验证的验收标准，不是已验证结论。
> 实现阶段每验一项，在下方表格里改成实测数字并标注日期；未验证的保持 ⏳。

## Prerequisites

```bash
# 1. 基础设施
docker compose -p aw -f deploy/docker-compose.yml up -d postgres redis

# 2. 迁移
cd backend && .venv/bin/alembic upgrade head

# 3. 测试库（pytest 必须显式指向 _test 库）
export DATABASE_URL='postgresql+asyncpg://aw:test-password-123@localhost:5432/agent_workbench_test'
```

- 准备 admin / member / viewer 三类账号（`backend/scripts/bootstrap.py`）。
- 准备一个启用中的 Agent 与一个含 2+ 成员的 Agent 组。

## 1. 规则管理与下次触发时间（US1）

1. `POST /api/v1/schedules`，`cron_expr="0 9 * * 1-5"`，`timezone="Asia/Shanghai"`。
2. 预期：`201`，`next_run_at` 落在**下一个工作日** 09:00（当天已过 09:00 时为次日；
   周五之后为下周一）。
3. `POST` 同一接口，`cron_expr="0 0 31 2 *"`。
4. 预期：`422`，说明该表达式不会产生触发时刻。
5. `POST`，`cron_expr="60 * * * *"`。
6. 预期：`422`，指向 `cron_expr` 字段。
7. `PATCH` 该规则 `enabled=false` → 预期 `next_run_at=null`；再 `enabled=true` →
   预期 `next_run_at` **从当前时刻重算**，不早于 now。
8. 用 viewer 调 `POST`/`PATCH` → `403`；用 admin 调 `PATCH` 他人规则 → `403`；
   用 member 调 `GET /schedules/{他人id}` → `404`（**不是** 403）。

## 2. 触发建单与分配（US2）

> 用 `now` 注入而不是真的等，见 §7。手工验证时可用每 5 分钟规则并等到点。

1. 让一条指向**单 Agent** 的规则到期。
2. 预期：新增 1 张需求（`status=running`）、产生 Run，且
   `Run.assignee_type=agent`、`strategy=direct`。
3. 让一条指向**组 + fan_out** 的规则到期。
4. 预期：父 Run + N 子 Run，`parent_run_id` 正确；用同一组手工拖拽一次，
   **逐字段比对**两者结构（SC-003）。
5. 对 `pipeline` / `router` 重复第 3–4 步，`pipeline` 预期只入队 `sequence=1`。
6. 查 `GET /requests/{id}` → 预期含 `source_schedule_id` 与其名称。
7. 让创建者的月配额耗尽后触发 → 预期 `failed`、`reason=本月配额已用尽`、
   **无新需求**、且不重试。

## 3. 幂等（最容易假通过的一项）

1. 把某规则的 `next_run_at` 手动改回已处理过的计划时刻（SQL 直接改，模拟重复投递）。
2. 再跑一次扫描。
3. 预期：`schedule_triggers` 因 `UNIQUE(schedule_id, planned_at)` 冲突而不插入，
   **需求数不变**（仍为 1）。
4. **反向控制**：临时去掉唯一约束 → 重跑 → 预期需求变成 2。
   验完 `git checkout --` 还原，**不提交**。

## 4. 错过不补跑（US3，本特性最重要的行为）

> 不要真的等 2 小时。用注入的 `now` 跳过时间。

1. 一条每 5 分钟的规则，`next_run_at=01:00`。
2. 以 `now=03:00` 调用一次扫描。
3. 预期：
   - 新需求数 **0**
   - `schedule_triggers` 新增 **1 条** `result=missed`，`missed_count=25`（实测值）
   - `window_start=01:00`、`window_end=03:00`
   - `next_run_at` 推进到 `03:05`
4. 对比项：`now=01:00:45`（延迟 45 秒，小于 90 秒宽限期）→ 预期 `fired`，**不是** `missed`。
5. `now=01:09`（每 5 分钟规则）→ 预期 1 条 `missed`、`missed_count=2`。
6. **反向控制**：把「错过优先」改成「先判忙」，再用「前次需求仍 running + 停机 2 小时」
   组合触发 → 预期记录变成 `skipped_busy`（即需求被掩盖）。这证明判定顺序不是可有可无的。

## 5. 判定顺序与失败分类

1. 前次需求 `running` 时到点 → 预期 `skipped_busy`、`reason=上次运行尚未结束`、无新需求。
2. 前次需求 `completed` 后到点 → 预期正常 `fired`。
3. 归档目标 Agent 后到点 → 预期 `failed`、`reason=目标已归档`。
4. 删除目标 Agent 后到点 → 预期 `failed`、`reason=目标已删除`。
5. 把规则目标改成他人 Agent（直连 SQL 改 `target_id`）后到点 →
   预期 `failed`、`reason=目标不可用`，**不得**出现"存在但无权"字样。
6. 停用创建者后到点 → 预期 `failed`、`reason=创建者已停用`。
7. 连续失败 5 次 → 预期 `enabled=false`、`auto_disabled_reason` 非空，
   且之后**不再产生任何记录**。
8. 规则停用状态下到点 → 预期**零记录**（停用 ≠ 跳过）。
9. `reason` 抽查：不得出现 UUID、表名、异常类名、堆栈（`grep` 断言）。

## 6. 变更不污染历史（US5）

1. 规则触发一次 → 产生需求 A（记下目标与标题）。
2. `PATCH` 改目标与 `template.title` → 再触发 → 产生需求 B。
3. 预期：需求 A 的 `source_schedule_id`、标题、Run 的 `assignee_id` **全部不变**；
   需求 B 使用新配置（SC-009）。
4. `DELETE` 规则 → 预期 `204`；需求 A/B 与它们的 Run **仍然存在**，
   `source_schedule_id` 置 `NULL`；触发器历史随规则级联删除。
5. **反向控制**：把外键改成 `ON DELETE CASCADE` → 删规则 → 预期需求被连带删除。
   验完还原。

## 7. 时间注入（如何在不等待的情况下验证）

调度服务入口接受 `now` 参数；Beat 任务用当前时间。验证时**直接调用服务函数**，
不经过 Beat：

```bash
cd backend && .venv/bin/python - <<'PY'
import asyncio, datetime as dt
from zoneinfo import ZoneInfo
from app.core.db import SessionLocal
from app.services.schedule_service import run_due_schedules

async def main():
    async with SessionLocal() as db:
        n = await run_due_schedules(db, now=dt.datetime(2026,11,17,3,0,tzinfo=ZoneInfo("Asia/Shanghai")))
        print("processed:", n)
asyncio.run(main())
PY
```

> 这不等于验证了 Beat 真的每分钟在跑。Beat 的接线必须在真实栈单独确认（见 §9），
> 否则「服务函数对」但「没人调它」会被漏掉。

## 8. 迁移往返

```bash
# 必须用空库：测试套件留下的库有对象但 alembic_version 为空，会误判为 base
docker exec aw-postgres psql -U aw -c 'CREATE DATABASE aw_migration_probe'
cd backend
DATABASE_URL='postgresql+asyncpg://aw:test-password-123@localhost:5432/aw_migration_probe' \
  .venv/bin/alembic upgrade head
DATABASE_URL='...' .venv/bin/alembic downgrade base
DATABASE_URL='...' .venv/bin/alembic upgrade head
docker exec aw-postgres psql -U aw -c 'DROP DATABASE aw_migration_probe'
```

预期：7 个既有迁移 + 新增 1 个 = **8 个** up / 8 个 down / 8 个 up，最终 `head`，
两个新 enum 在 downgrade 后被删除。
（核实：`alembic heads` 当前为 `0006_artifact_run_step_index`，`versions/` 下实际 7 个迁移文件。）

## 9. 真实栈端到端（不能省）

1. 起完整栈（含 `beat`），确认 `aw-beat` 健康。
2. 建一条每分钟规则，**不做任何注入**，等 3 分钟。
3. 预期：日志出现 3 次扫描处理；需求数 3 或（若执行超过 1 分钟）出现 `skipped_busy`。
4. 停掉 `aw-beat` 5 分钟再启动 → 预期恢复后**不补跑**，产生 1 条 `missed`。
5. 观察 `aw-beat` 容器 CPU/内存（限额 0.5 CPU / 256MB）是否够用。

## 10. 性能与并发（SC-006）

1. 20 条规则设为同一分钟到期。
2. 用既有归因口径（含 150ms 绝对上限）测 `/requests` 与 `/runs/{id}` 的 P95。
3. 预期：20 条全部到达确定结果（`fired` 或带原因的跳过/失败），无静默丢失；
   核心接口失败请求数 0。
4. **如实记录**：这是毫秒级测量，在共享 runner 上会抖；判据沿用 002/003，
   不另立数字。若比值超限需同时看绝对 P95。

## 验证记录表

| 场景 | 判据 | 状态 | 实测 / 备注 |
|---|---|---|---|
| S1 下次触发时间 | 下一工作日 09:00 | ⏳ | |
| S1 非法表达式 | 422 + 字段定位 | ⏳ | |
| S1 权限矩阵 | viewer 403 / admin 写 403 / 越权 404 | ⏳ | |
| S2 Run 结构一致 | 与手工分配逐字段相同 | ⏳ | |
| S2 配额耗尽 | failed + 无需求 + 不重试 | ⏳ | |
| S3 幂等 | 需求数仍为 1 | ⏳ | |
| S4 停机 2 小时 | 0 新需求 + 1 条 missed(25) | ⏳ | |
| S4 延迟 45 秒 | fired（非 missed） | ⏳ | |
| S5 判定顺序反向控制 | 变 skipped_busy（证明顺序必要） | ⏳ | |
| S5 失败分类 | 5 种 reason 各自命中 | ⏳ | |
| S5 自动停用 | 5 次后停用且不再记录 | ⏳ | |
| S6 历史不变 | 需求 A 未被改写 | ⏳ | |
| S6 删规则保留需求 | 需求与 Run 仍在 | ⏳ | |
| S8 迁移往返 | up/down/up 各 8 | ⏳ | |
| S9 Beat 真实接线 | 每分钟真的在跑 | ⏳ | |
| S9 Beat 停机恢复 | 不补跑 | ⏳ | |
| S10 20 并发 | 全部确定结果 + 无接口失败 | ⏳ | |
