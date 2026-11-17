---

description: "Implementation tasks for scheduled requests: cron rules, automatic request creation and assignment, miss handling, history"
---

# Tasks: 定时调度（按规则自动建单并分配执行）

**Input**: Design documents from `/specs/004-scheduled-requests/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/api.md`, `quickstart.md`

**Approach**: TDD。每个业务任务前先写失败的测试。每个 Phase 独立提交，通过后再进入下一 Phase。
002/003 已完成（各自 `tasks.md` 全勾选），本特性不补做它们的任何未完成项。

## 贯穿全清单的硬约束（Notes）

这些约束在多个任务里反复出现，统一记在这里，避免每个任务重复一遍：

1. **`crontab` 的位置参数顺序是 `minute, hour, day_of_week, day_of_month, month_of_year`**
   —— `day_of_week` 在 `day_of_month` **之前**。必须用**关键字参数**，否则按标准 cron 顺序
   传位置参数会解析错（实测抛 `Invalid weekday literal`）。
2. **`crontab` 的 `tz` 不是构造参数**（传 `tz=` 抛 `TypeError`），只能对**实例**赋值。
3. **周期表达式语义是「日期 ∩ 星期」**，不是标准 cron 的并集（实测 `0 9 1 * 1` 只在既 1 号又周一触发）。
4. **不让 API 层承担触发逻辑**：触发只在 Worker 侧的服务函数里，API 只做规则 CRUD。
5. **不新增依赖**：不用 croniter、不用 APScheduler、不用 django-celery-beat。
6. **不改 `assign()` 的事务边界**：调度复用它的内部 `commit`（先 `flush` 需求再调）。
7. **不改 `requests` 既有列语义**：只加一个可空 `source_schedule_id`（`ON DELETE SET NULL`）。
8. **`schedule_triggers` 的唯一约束 `(schedule_id, planned_at)` 是幂等基石**，任何实现都不得绕过。
9. **判定顺序固定**：错过（第 3 步）**必须**在「前次未结束」（第 4 步）之前。
10. **未验证的不得勾选**：任何需要真实栈/浏览器/时间流逝的项，未做就保持 `[ ]` 并在 Notes 写明原因。
11. **每个 Phase 末跑门禁**：`ruff check` / `ruff format --check` / `mypy` / `pytest`；
    涉及前端时 `eslint` / `tsc` / `vitest` / `build`。

## Phase 1: Setup

**Purpose**: 配置项与环境示例，不含业务逻辑。

- [X] T001 [P] Add `schedule_*` settings with safe defaults in `backend/app/core/config.py`: `schedule_scan_enabled` (bool, default true), `schedule_missed_grace_seconds` (int, default 90), `schedule_default_timezone` (str, `Asia/Shanghai`), `schedule_max_failures` (int, default 5), `schedule_history_max_rows` (int, default 500), `schedule_history_retention_days` (int, default 90), `schedule_batch_size` (int, default 200).
- [X] T002 [P] Document the same keys in `deploy/.env.example` with comments explaining each bound and why the grace period is 90s (scan period 60s + one processing margin).
- [X] T003 [P] Add audit action constants/helpers for `schedule_create`, `schedule_update`, `schedule_delete`, `schedule_enable`, `schedule_disable`, `schedule_trigger` in `backend/app/core/audit.py` (follow existing action naming; keep the reason strings user-facing and free of identifiers).

## Phase 2: Foundational — cron pure functions

**Purpose**: 把最容易出错的「下次触发时间」算法做成**无 IO 的纯函数**，可低成本穷举验证。
不依赖数据库，可先行完成并独立验证。

- [X] T004 [P] Write failing unit tests in `backend/tests/unit/test_schedule_cron.py` covering: 5-field validation; unsupported forms (`@daily`, 6 fields) rejected; `60 * * * *` rejected; `0 0 31 2 *` rejected as never-occurring; weekday `0` = Sunday and `1-5` = Mon-Fri mapping; **intersection semantics** (`0 9 1 * 1` must only yield dates that are both the 1st and a Monday); leap-year (`0 0 29 2 *` from 2026-03-01 → 2028-02-29); weekend crossing (`0 9 * * 1-5` from Fri 18:00 → next Mon 09:00); exact-hit advance (`0 9 * * *` at 09:00 → **next day** 09:00, not same day); sub-minute truncation.
- [X] T005 [P] Write failing unit tests for `count_between` in the same file: 5-minute rule from 01:00 to 03:00 → **25**; 01:00 to 01:09 → **2**; 01:00 to 01:00:45 → **1**; and a guard that a pathological window cannot loop unbounded (returns a bounded count or raises a clear error).
- [X] T006 Implement `backend/app/services/schedule_cron.py`: `parse_cron(expr, tz)` (keyword args only — add a comment explaining the `day_of_week`-before-`day_of_month` trap and the instance-assignment `tz` trap), `next_after(cron, after)`, `count_between(cron, start, end)`, `validate_cron(expr, tz, now)` returning a user-facing Chinese error message on failure. No database, no settings import beyond the default tz.
- [X] T007 Verify T004/T005 pass and that `validate_cron` produces the exact `missed_count` numbers used in `contracts/api.md` (25 / 2 / 1). Record the measured numbers in the commit message.

**Checkpoint**: cron 算法在无数据库条件下全部通过；三个实测数字与契约一致。提交 Phase 2。

## Phase 3: Foundational — persistence

**Purpose**: 两张新表 + `requests` 一个可空外键。阻塞所有用户故事。

- [X] T008 [P] Write failing tests in `backend/tests/integration/test_schedule_schema.py` for: `(schedule_id, planned_at)` uniqueness violation; `CHECK (not enabled or next_run_at is not null)` rejecting an enabled rule with null `next_run_at`; `ON DELETE SET NULL` on `requests.source_schedule_id` when the schedule is deleted; `ON DELETE CASCADE` from `schedules` to `schedule_triggers`; and that deleting a schedule does **not** delete its requests or runs.
- [X] T009 Implement `backend/app/models/schedule.py` with `Schedule` and `ScheduleTrigger` per `data-model.md`, including both enums, the unique constraint, the partial index `WHERE enabled`, and the `CHECK` constraints. Export from `backend/app/models/__init__.py`.
- [X] T010 Add `source_schedule_id` (`ForeignKey("schedules.id", ondelete="SET NULL")`, nullable, indexed) plus the relationship to `backend/app/models/request.py`. Do not touch any existing column.
- [X] T011 Write `backend/alembic/versions/0007_schedules.py`: create both enum types, both tables with constraints/indexes, and the `requests` column + FK + index. `downgrade` reverses in order and explicitly `DROP TYPE` both enums (follow the 002/003 convention).
- [X] T012 Run the migration round-trip on an **empty** database (`upgrade head → downgrade base → upgrade head`) and record the result. Do not use a database left by the test suite (it has objects but an empty `alembic_version`, which misreports as base).
- [X] T013 [P] Add schemas in `backend/app/schemas/schedule.py`: `ScheduleTargetType`, `ScheduleTriggerResult` enums; `ScheduleTemplate`, `ScheduleTarget`; `ScheduleCreate`, `ScheduleUpdate` (partial), `ScheduleOut` (with display-only `target.name` / `target.status`, `owner_email` for admin only), `TriggerOut`, `TriggerListOut` (with `counts`). Keep `target.status` supporting `missing` to distinguish deleted from archived.

**Checkpoint**: 迁移往返通过；约束测试全绿（含两条 `ON DELETE` 语义）。提交 Phase 3。

## Phase 4: User Story 1 — 规则 CRUD 与下次触发时间 (P1) 🎯 MVP 前半

**Goal**: 成员能建/改/启停/删自己的规则并看到可信的下次触发时间；admin 只读。
**Independent Test**: 建「工作日 9 点」规则返回下一工作日 09:00；非法表达式被拒并定位字段。

### Tests first

- [X] T014 [P] [US1] Write failing API contract tests in `backend/tests/integration/test_schedules_api.py` for the full permission matrix from `contracts/api.md`: viewer 403 on all; member 200/201/204 only on own; admin 200 on all reads but **403 on PATCH/DELETE of others**; and **cross-user GET returns 404, not 403**.
- [X] T015 [P] [US1] Extend the same file with the 6-step validation order: shape (422) → cron parse (422, `field=cron_expr`) → timezone unknown (422, `field=timezone`) → target unavailable (404) → strategy/target-type incompatibility (400). Assert that a wrong-order implementation would fail (e.g. an invalid cron with a bad target must report the **cron** error, since cron is validated first).
- [X] T016 [P] [US1] Extend the same file with `next_run_at` recomputation rules: changing `cron_expr`/`timezone`/`enabled` recomputes; changing `template`/`target` does **not**; disable sets null; re-enable recomputes **from now** (not from the old value), so a 3-day-disabled rule does not fire a compensating trigger.

### Implementation

- [X] T017 [US1] Implement rule CRUD + validation in `backend/app/services/schedule_service.py`: `create_schedule`, `update_schedule`, `set_enabled`, `delete_schedule`, `list_schedules`, `get_schedule`. Enforce the 6-step order, the uniform 404 for cross-user access, and recompute `next_run_at` per T016.
- [X] T018 [US1] Implement endpoints in `backend/app/api/v1/schedules.py` and register in `backend/app/main.py`. Use the existing `require_role` / `ResourceId` deps; admin read-only enforcement lives in the service, not only the router.
- [X] T019 [US1] Write audit records for create/update/delete/enable/disable with the caller as actor and **no** internal identifiers in `detail` beyond ids.
- [X] T020 [US1] Run Phase gate: `ruff check` / `ruff format --check` / `mypy` / `pytest backend/tests/integration/test_schedules_api.py` / `test_schedule_cron.py`.

**Checkpoint**: US1 的 Independent Test 通过。提交 Phase 4。至此「能定义规则并看到下次时间」可用。

## Phase 5: User Story 2 — 触发建单与分配 (P1) 🎯 MVP 后半

**Goal**: 到点自动建单并按目标/策略分配，Run 结构与手工分配 100% 一致。
**Independent Test**: 每分钟规则到期后出现 `running` 需求与正确 Run 结构，可追溯到来源规则。

### Tests first

- [X] T021 [P] [US2] Write failing tests in `backend/tests/integration/test_schedule_dispatch.py` for the `fired` path across all four target shapes: `agent` (strategy `direct`), `group`+`fan_out` (parent + N children, `parent_run_id`), `group`+`pipeline` (only `sequence=1` enqueued), `group`+`router` (single parent, `assignee_type=group`).
- [X] T022 [P] [US2] Add the **structural equivalence** test (SC-003): perform the same assignment by hand against the same target and compare Run rows field-by-field (assignee_type, assignee_id, strategy, sequence, parent_run_id, `tool_grants_snapshot` presence). This is the test that prevents the scheduled path from silently degrading.
- [X] T023 [P] [US2] Add failing tests for atomicity (FR-009): make `assign()` fail (archived target, quota exhausted) and assert **no `requests` row survives** and exactly one `failed` trigger record exists with `next_run_at` advanced.
- [X] T024 [P] [US2] Add a test that `GET /requests/{id}` now returns `source_schedule_id` and its name, and that existing 001 request-contract assertions still pass (the new fields must be optional/null by default).

### Implementation

- [X] T025 [US2] Implement `fire_schedule(db, schedule, *, now)` in `backend/app/services/schedule_service.py` for the `fired` branch: build `Request` from template → `db.add` → `await db.flush()` → call `assignment_service.assign(...)` (reuse, do not reimplement). Add a comment explaining that `assign()` commits internally (R-05) and why `flush` (not `commit`) precedes it.
- [X] T026 [US2] Implement the failure re-open path: catch `AppError` from `assign()`, roll back the whole transaction (including the trigger placeholder), then **open a new transaction** to write the `failed` record and advance `next_run_at`. Without the new transaction the record is rolled back with the rest.
- [X] T027 [US2] Implement `run_due_schedules(db, *, now=None)` scanning `enabled` rules with `next_run_at <= now`, taking a per-rule `SELECT ... FOR UPDATE`, and isolating each rule's exception so one failure cannot abort the batch (FR-018). Support `now` injection for tests.
- [X] T028 [US2] Implement the trigger placeholder insert with `ON CONFLICT (schedule_id, planned_at) DO NOTHING` **before** creating the request, and abort the rule if the insert conflicted (this is the idempotency mechanism, R-03).
- [X] T029 [US2] Add the Celery task wrapper in `backend/app/tasks/schedule.py` and register `schedule-scan` (every minute) in `backend/app/tasks/celery_app.py`. Skip the scan when `schedule_scan_enabled` is false.
- [X] T030 [US2] Expose `next_run_at`/`last_run_at`/`last_result` updates atomically with the trigger record (same transaction as the placeholder), so a crash cannot leave `next_run_at` pointing at an already-processed instant.
- [X] T031 [US2] Run Phase gate and record the equivalence-test result explicitly.

**Checkpoint**: US2 的 Independent Test 通过；SC-003 的结构比对测试绿。提交 Phase 5。
**至此 MVP 完整**：能定义规则且到点自动执行。

## Phase 6: User Story 3 — 错过、跳过、失败与自动停用 (P1)

**Goal**: 停机后不补跑、合并记录；忙则跳过；失败可读并自动停用。
**Independent Test**: 注入「停机 2 小时」→ 0 新需求 + 1 条 `missed_count=25`。

### Tests first

- [X] T032 [P] [US3] Write failing tests for the **judgment order** in `backend/tests/integration/test_schedule_judgement.py`, one test per row of the 22-row table in `contracts/api.md`. The decisive one: 「前次需求仍 running **且** 停机 2 小时」must yield `missed` (not `skipped_busy`); a wrong order must fail this test.
- [X] T033 [P] [US3] Add failing tests for missed-window merging: 5-minute rule, 01:00→03:00 yields exactly **one** record with `missed_count=25`, `window_start`/`window_end` set, `fired_at` null, and **zero** new requests; 01:00→01:09 yields `missed_count=2`; 01:00→01:00:45 (under the 90s grace) yields `fired`, not `missed`.
- [X] T034 [P] [US3] Add failing tests for the failure taxonomy and reasons: archived target, deleted target, other-user's target (**reason must not distinguish "exists but forbidden"**), quota exhausted (no retry), disabled creator; then auto-disable after `schedule_max_failures` with `auto_disabled_reason` set and **no further records**.
- [X] T035 [P] [US3] Add a test asserting a disabled rule produces **zero** trigger rows at its would-be time (disable ≠ skip).
- [X] T036 [P] [US3] Add a test that a batch containing one raising rule still processes the remaining rules (FR-018).

### Implementation

- [X] T037 [US3] Implement the `missed` branch: compute `missed_count` via `count_between(planned_at, now)`, write one merged record, advance `next_run_at` to `next_after(now)`, create nothing.
- [X] T038 [US3] Implement the `skipped_busy` branch by checking whether the request from the schedule's most recent `fired` trigger is in a non-terminal state (R-06 — judge the schedule's own last request, not the target's global busyness).
- [X] T039 [US3] Implement the `failed` branch with the reason whitelist from `contracts/api.md` §2, increment `consecutive_failures`, auto-disable at threshold, and reset the counter on `fired`/`skipped_busy`.
- [X] T040 [US3] Add the reason-string guard test/assertion: no UUID, table name, exception class or stack in `reason` (grep-style assertion over produced reasons).
- [X] T041 [US3] Run Phase gate; verify the three measured counts (25 / 2 / 1) match the contract.

**Checkpoint**: US3 的 Independent Test 通过。提交 Phase 6。

## Phase 7: User Story 4 + 5 — 历史查询与变更不污染历史 (P2)

**Goal**: 触发历史可查可跳转；改/删规则不改写既有需求与运行。

### Tests first

- [X] T042 [P] [US4] Write failing tests in `backend/tests/integration/test_schedule_history.py` for: pagination, `result` filter, `counts` computed over the **whole** schedule (not the current page/filter), ordering `planned_at DESC, id DESC` (includes a case with two rows sharing `planned_at` to prove the second key matters), and that admin can read others' history while member cannot.
- [X] T043 [P] [US5] Write failing tests for immutability: after `PATCH` of target+template, the earlier request's title/`source_schedule_id` and its Run's `assignee_id` are unchanged (SC-009); after `DELETE`, requests/runs survive with `source_schedule_id` nulled while trigger rows are gone.
- [X] T044 [P] [US5] Add a concurrency test: `PATCH` racing with a trigger processing must yield either the fully-old or fully-new config, never a mix (assert the request's title and its Run's assignee agree with each other).

### Implementation

- [X] T045 [US4] Implement `GET /schedules/{id}/triggers` with pagination, filter, and the `counts` `GROUP BY` query in `backend/app/api/v1/schedules.py`.
- [X] T046 [US5] Ensure `PATCH` recomputes only what T016 specifies and never writes back to existing requests; ensure `DELETE` relies on the FK behaviours from `data-model.md` (no manual cascading of requests).
- [X] T047 [US5] Implement history retention in `backend/app/tasks/maintenance.py` (keep last `schedule_history_max_rows` per schedule and `schedule_history_retention_days`), register it in the daily beat schedule beside the sandbox purge, and keep the summary counters on `schedules` so retention does not lose "last fired / totals".
- [X] T048 [US4] Run Phase gate.

**Checkpoint**: US4/US5 Independent Tests 通过。提交 Phase 7。

## Phase 8: Frontend

**Goal**: `/schedules` 页可建/改/启停/删规则，看触发历史并跳到需求。

### Tests first

- [X] T049 [P] Write failing Vitest tests in `frontend/src/tests/schedules.test.tsx` for: form validation surfacing the cron error; the cron semantics hint being visible (intersection wording); viewer not seeing the create entry; history drawer rendering all four results with distinct labels and a working jump to the request.
  - 路径偏差（本阶段修正）：原文写 `frontend/tests/`，仓库实际是 `frontend/src/tests/`（与 `external-lifecycle.test.tsx` 同目录，`vite.config.ts` 的 `test` 段只覆盖 `src/`）。
  - 实际 8 条测试，比原文多一条 `BoardPage · 深链打开需求`：drawer 的「查看需求」跳 `/?request=<id>`，若看板不消费该参数，那个跳转就是死链——闭环的最后一环必须有测试守着。
- [X] T050 [P] Add API wrappers and DTOs for schedules/triggers in `frontend/src/api/endpoints.ts` following the existing pattern (typed, no `any`).

### Implementation

- [X] T051 [US1] Implement `ScheduleForm.tsx` (template fields, target picker reusing Agent/Group lists, cron input with the semantics hint, timezone defaulting to `Asia/Shanghai`) and `ScheduleList.tsx` (status, next/last run, last result, enable toggle, delete confirm).
- [X] T052 [US3/US4] Implement `TriggerHistory.tsx` drawer: four result labels, `missed_count`/window display, reason text, and jump to request detail.
- [X] T053 [US1] Add `frontend/src/pages/SchedulesPage.tsx`, route `/schedules` with `lazy()`, and a nav entry in `AppLayout.tsx`. Show the entry to member/admin only.
- [X] T054 [US2] Show the schedule origin on the request side: a badge/link in the request detail (or card) when `source_schedule_id` is present. Do not alter the board columns.
- [X] T055 Run frontend gate: `eslint`, `tsc`, `vitest`, `npm run build`; record bundle impact against the current baseline (first-screen gzip 102KB).

**Checkpoint**: 前端测试绿；页面可完成建规则→看历史→跳需求的闭环。提交 Phase 8。

> Phase 8 实测（2026-11-17）：`vitest` 88 项全绿；`eslint` 0 error（2 个既有 fast-refresh warning，来自 `Toast.tsx`/`ConfirmDialog.tsx`，非本阶段引入）；`tsc --noEmit` 通过；`npm run build` 成功。
> Bundle：首屏 `dist/assets/index-*.js` **102.04 KB gzip**，与基线 102KB 持平——`/schedules` 拆成独立 chunk（`SchedulesPage-*.js` 7.94 KB gzip），未落到首屏。
> 反向验证 5 次（均已还原、未提交）：① cron 错误不按 `detail.field` 归属 → 污染无关输入框的用例转红；② 删掉交集措辞 → 语义提示用例转红；③ 把 `missed`/`skipped_busy` 文案合并成「未执行」→ 四态可辨用例转红；④ 去掉 viewer 的 403 分支 → 写入口隐藏用例转红；⑤ 看板不消费 `?request=` → 深链用例转红。

## Phase 9: Polish & Honest Verification

**Purpose**: 真实栈验收与文档，**未验证的不得勾选**。

- [X] T056 [P] Run the real-stack Beat wiring check (quickstart §9): start full stack, create a per-minute rule, wait 3 minutes **without** injection, confirm the scan actually runs and requests appear. This is the item that a service-function test cannot cover.
- [X] T057 [P] Run the stop-and-restart check (quickstart §9 step 4): stop `aw-beat` for ~5 minutes, restart, confirm **no catch-up** and one `missed` record.
- [X] T058 [P] Run the idempotency reverse control (quickstart §3 step 4): drop the unique constraint temporarily, confirm duplicate requests appear, then restore with `git checkout --` and **do not commit** the change.
- [X] T059 [P] Run the judgment-order reverse control (quickstart §5 step 6): swap the missed/busy order, confirm the misleading `skipped_busy`, then restore and do not commit.
- [X] T060 [P] Run the SC-006 concurrency check: 20 rules due in the same minute, using the existing attribution criteria (including the 150ms absolute ceiling); record both the ratio and absolute P95, and state the host contention honestly.
- [X] T061 [P] Security sweep: `git grep` for secrets; confirm `reason` strings contain no identifiers; confirm RBAC matrix holds end-to-end; confirm audit rows exist for all six actions plus each trigger.
- [X] T062 Update `specs/004-scheduled-requests/quickstart.md` verification table with real numbers and dates; leave anything unrun as ⏳ with a reason.
- [X] T063 Update `README.md` (remove "定时调度尚未实现" from 已知限制), `docs/architecture.md` (schedules table now real; note the cron intersection semantics), and `deploy/README.md` (new env keys, beat scan).
- [X] T064 Reconcile spec vs implementation: for every deviation found during Phases 4–9, amend the upstream document **in the same phase**, and list them in the commit message. Do not batch them to the end.
- [X] T065 Final gate: `ruff check` / `ruff format --check` / `mypy` / full `pytest` (record per-module counts, not just a total) / frontend `eslint` / `tsc` / `vitest` / `build` / migration round-trip / `git diff --check` / `git status`.

**Checkpoint**: 所有可执行的验证项已完成并记录实测值；不可执行的项明确标 ⏳ 并写明原因。

> Phase 9 终局门禁实测（2026-11-17）：
>
> | 检查 | 结果 |
> |---|---|
> | `ruff check app tests` | All checks passed |
> | `ruff format --check .`（**口径对齐 CI，覆盖 alembic/**） | **232 files already formatted** |
> | `mypy app` | no issues in 117 source files |
> | `pytest tests`（排除 `test_mcp_fixture_http.py`） | **1033 passed, 1 error, 6 subtests passed, 0 failed** |
> | 调度模块专项（6 个文件） | 108 passed |
> | 前端 `tsc --noEmit` | 通过 |
> | 前端 `eslint src` | 0 error / 2 warning（均为既有 `ui/` 组件的 `react-refresh` 提示） |
> | 前端 `vitest run` | 16 files / 88 tests passed |
> | 前端 `npm run build` | 成功，首屏 `index-*.js` **102.04 KB gzip**；`SchedulesPage` 独立分包 7.94 KB gzip |
> | 迁移往返（空库，按 CI 口径） | up 8 → **`downgrade base` 8**（业务表清零、残留仅 `alembic_version`、枚举 0）→ 再 up 8 → `current` = `0007_schedules (head)`；另测到 0006 时 2 个新枚举确认删除 |
> | `git diff --check` | 无空白错误 |
>
> **⚠️ 门禁口径曾不完整，已修正**：最初记的是 `ruff format --check app tests`（**222** files），
> 而 CI 实际口径是 `ruff format --check .`（`ci.yml:186`，覆盖整个 `backend/` 含 `alembic/`）。
> 按 CI 口径复跑时暴露出 `0007_schedules.py` 从 T008 起就带着不合格式的排版，
> **这条本来会让 CI 红**。已格式化（纯排版、无语义变化，并在空库上实测 `upgrade head`
> 全 8 个 revision 通过），数字随之变为 **232**。教训：门禁口径必须照抄 CI，
> 「少查一个目录」等于没查。
>
> **排除项说明**：`test_mcp_fixture_http.py` 因沙箱临时目录 `pytest-of-unknown` 已存在而
> 报 `FileExistsError`（非代码问题）；该文件与调度无关，单独跑亦不涉本特性。
> 真实栈验收用的 `aw_004_verify` / `aw_004_roundtrip` 为本次新建，**未触碰开发库
> `agent_workbench`**；验收结束后这些临时库已全部删除，环境已复原。

## Dependencies & Execution Order

- Phase 1 → Phase 2 → Phase 3 顺序执行；Phase 2 与 Phase 3 之间无代码依赖，但 Phase 3 的 schema 字段需要 Phase 2 的错误文案。
- US1（Phase 4）依赖 Phase 3；US2（Phase 5）依赖 US1（需要规则存在）；US3（Phase 6）依赖 US2（需要一个能跑的 `fired` 分支做对照）。
- US4/US5（Phase 7）依赖 US3 的记录形态稳定。
- 前端（Phase 8）可在 Phase 4 之后并行开展（只依赖契约），但历史抽屉需要 Phase 7 的 `counts`。
- Phase 9 依赖全部。

## Parallel Opportunities

- T001–T003 并行。T004–T005 并行。T008 与 T013 并行（测试与 schema 可同时写，但 schema 要等 T009 的模型）。
- Phase 4–7 内各 `[P]` 测试任务可并行编写，实现必须串行（同一文件）。
- Phase 8 的 T049/T050 可并行；T051/T052 可并行（不同组件）。
- Phase 9 的 T056–T061 中，T058/T059 涉及临时改动代码，**必须串行**且不得与其他验证同时进行。

## Implementation Strategy

1. 先做 Phase 1–3（无业务风险的地基），提交。
2. Phase 4 + 5 一起构成 MVP，两阶段分别提交但连续完成；若时间有限，在这里停下也是可上线的状态。
3. Phase 6 是本特性的技术核心（判定顺序 + 不补跑），不要与 Phase 5 合并提交——出问题时需要单独回滚。
4. Phase 7–9 增量推进，每个 Phase 一次提交。
5. 任何一项验证没做，就不勾选，并在 Notes 里写明「用什么动作才能验、为什么现在没验」。
