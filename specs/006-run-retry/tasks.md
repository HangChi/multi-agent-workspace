# Feature 006 Tasks: 运行自动重试

> **Branch**: `feat/006-run-retry` | **Date**: 2026-11-18
> **Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/api.md](contracts/api.md), [checklists/requirements.md](checklists/requirements.md)

分支 `feat/006-run-retry` 从 `master` 切出；完成后 fast-forward 合并回 `master` 并删除分支（宪法分支约定）。

图例：`[P]` = 可并行；`[US<n>]` = 所属用户故事；`TDD` = 先写测试（红）再实现（绿）。

---

## Phase 1：规格（已完成）

- [X] T001 Write `specs/006-run-retry/spec.md`：4 个用户故事、21 条 FR、6 条 SC、5 项范围边界、6 条假设。
- [X] T002 Write `specs/006-run-retry/research.md`（R-01..R-08）与 `plan.md`（Technical Context / Constitution Check / 设计概要 / Phase / 风险）。
- [X] T003 [P] Write `specs/006-run-retry/data-model.md` 与 `contracts/api.md`（响应体新字段、手动重试成本口径变更、`run_retrying` WS 事件、审计动作、配置项）。
- [X] T004 [P] Write `specs/006-run-retry/checklists/requirements.md`（Content Quality / Completeness / Readiness / 宪法对齐 / SC→FR / FR→US）。
- [X] T005 Point `.specify/feature.json` at `specs/006-run-retry`.

---

## Phase 2：后端（TDD）

**Checkpoint**: 瞬时失败自动重试可用，不重复执行，成本累计，全部门禁绿。

### 2.1 失败分类与退避（纯函数，先测后写）

- [X] T006 [P] [US1] Unit: `backend/tests/unit/test_retry_policy.py` — assert `classify_failure` 把连接类/超时/5xx 判为 `TRANSIENT`，把配额/成本/步数/超时/取消/策略拒绝判为 `PERMANENT`，把**未知异常**判为 `PERMANENT`（research R-05 的保守原则）。
- [X] T007 [P] [US1] Unit: 同上文件 — assert `backoff_seconds` 是指数增长（`base×2^attempt`）、带 full jitter（结果落在 `[0, 计算值]` 内、且同 attempt 多次调用结果不同）、不超过 `cap`、attempt 为 0 时等于基准。
- [X] T008 [US1] Write `backend/app/orchestrator/retry_policy.py`: `FailureKind` 枚举、`classify_failure(exc)`、`backoff_seconds(attempt, base, cap)`。纯函数，不 import IO 模块。

### 2.2 状态机与迁移

- [X] T009 [P] [US1] Unit: 扩充 `backend/tests/unit/test_state_machine.py` — assert `running → retrying`、`retrying → running`、`retrying → failed` 合法；`retrying → completed` 非法；`retrying` 不在终态集合；`retrying` 时迁移幂等。
- [X] T010 [US1] Modify `backend/app/orchestrator/state_machine.py`: 新增 `RunStatus.RETRYING` 与三条迁移；`_RETRYABLE_TERMINAL` 逻辑保持（手动重试仍从 failed/timeout 出发）。
- [X] T011 [US1] Write `backend/alembic/versions/0010_run_retry_fields.py`: `runs` 加 `attempt`(int, default 1)、`next_retry_at`(timestamptz, null)、`retry_history`(jsonb, default `[]`)；`run_status` 枚举 `ADD VALUE IF NOT EXISTS 'retrying'`（注意 PG 的 `ALTER TYPE` 事务限制，见 data-model §3）；downgrade 删列（枚举值不删，PG 不支持安全删除且旧代码可能仍引用）。

### 2.3 执行链路

- [X] T012 [P] [US1] Integration: `backend/tests/integration/test_run_retry.py` — 瞬时失败（LLM 连接错误）→ Run 变 `retrying`、`attempt=1`、`next_retry_at` 非空、`retry_history` 有一条记录、WS 推 `run_retrying`、审计有 `run_auto_retry`。
- [X] T013 [P] [US1] Integration: 同上 — 重试到点后执行成功 → `attempt=2`、状态 `completed`、成本 = 两次之和（SC-003）。
- [X] T014 [P] [US1] Integration: 同上 — 业务类失败（配额超限）→ 直接 `failed`，`attempt` 不变，**不**投递重试任务。
- [X] T015 [P] [US1] Integration: 同上 — 3 次尝试全部瞬时失败 → 终态 `failed`，`retry_history` 有 2 条，第 3 次后不再投递。
- [X] T016 [US2] Integration: 同上 — 锁占用时再次触发（模拟并发）→ 后到者 `busy`，不产生第二次执行、成本不加倍。
- [X] T017 [US2] Integration: 同上 — Run 已 `completed` 后延迟的重试消息到点 → 被丢弃，状态与成本不变（FR-011）。
- [X] T018 [US3] Integration: 同上 — 累计成本达到上限时第 3 次尝试被熔断（`cost_micros > limit×1M`），不会超支。
- [X] T019 [US1] Modify `backend/app/tasks/execute_run.py`: 失败分类 → 瞬时且 `attempt < max` 时写 `retry_history`/`next_retry_at`、`transition(RETRYING)`、发事件、写审计、`apply_async(countdown=wait)`、`return "retrying"`；耗尽或业务类走既有终态路径。重试任务消费时校验 `status == retrying`。
- [X] T020 [US1] Modify `backend/app/core/config.py`: 新增 `run_max_attempts` / `run_retry_base_seconds` / `run_retry_cap_seconds`，默认 3 / 2 / 60；同步 `deploy/.env.example`。
- [X] T021 [P] [US1] Modify `backend/app/core/audit.py`: 新增 `RUN_AUTO_RETRY = "run_auto_retry"` 并入动作集合。
- [X] T022 [P] [US4] Modify WS 事件模块：新增 `run_retrying` 事件（字段见 contracts §3）。

### 2.4 成本口径与手动重试

- [X] T023 [US3] Modify `backend/app/api/v1/runs.py`: 手动重试不再重置 `cost_cents` / `cost_micros`，改为递增 `attempt`；`error` / `current_step` 仍重置。
- [X] T024 [US3] Integration: 扩充 `backend/tests/integration/test_execution_flow.py` 的既有重试用例 — assert 重试后成本**累计**而非归零（SC-006：契约不变、口径变更）。
- [X] T025 [US1] Integration: reaper 不误杀 — 构造 `status=retrying` 且 `updated_at` 超阈值的 Run，assert reaper 不回收它（FR-012）。

### 2.5 查询面

- [X] T026 [P] [US4] Modify `backend/app/schemas/run.py` 与 `GET /runs/{id}`: 响应体加 `attempt` / `next_retry_at` / `retry_history`；对旧数据有默认值。
- [X] T027 [P] [US4] Integration: assert `GET /runs/{id}` 返回三个新字段且类型正确；旧 Run（迁移前创建）返回 `1` / `null` / `[]`。

**Checkpoint 2**: `pytest` 全绿 + `ruff check` + `ruff format --check` + `mypy app` 全绿。

---

## Phase 3：前端

**Checkpoint**: 重试进度在详情页与看板可见，不与「执行中/失败」混淆。

- [X] T028 [P] [US4] Unit(vitest): 状态徽章组件 — `retrying` 渲染为「重试中」且色调区别于 `failed`；未知状态降级显示原始字符串。
- [X] T029 [P] [US1] Unit(vitest): 倒计时 hook — 由 `next_retry_at` 计算剩余秒数，到点归零不变负；WS 事件到达时重置。
- [X] T030 [US4] Modify 运行详情页: 状态徽章支持 `retrying`；「执行」区块下增加重试历史折叠列表（attempt 倒序，含错误与时刻）；显示「第 N/M 次 · 约 Xs 后重试」。
- [X] T031 [US1] Modify WS 事件处理: 消费 `run_retrying`，更新对应 Run 的状态与倒计时。
- [X] T032 [P] [US1] Modify 看板/列表卡片: `retrying` 状态用琥珀色调，与 `failed`（红）明确区分。
- [X] T033 [P] [US1] Modify 前端 `Run` 类型与 API 封装: 补 `attempt` / `next_retry_at` / `retry_history` 三个字段（含迁移前旧数据的默认值处理）。

**Checkpoint 3**: `npm run lint` + `prettier --check` + `tsc --noEmit` + `vitest run` + `vite build` 全绿。

**Phase 3 实测记录（2026-11-18）**

| 项 | 结果 |
|---|---|
| `vitest` | 115 passed / 21 files（新增 RetryStatus 8 例） |
| `eslint` / `prettier --check` / `tsc --noEmit` / `vite build` | 全绿 |

**实现说明**：
- 状态元数据收敛到 `components/run/RetryStatus.ts`（含 `retrying` 与未知状态降级），
  RunDetailPage 的 STATUS_LABEL/STATUS_TONE 从它派生，避免页面里各写一份漏掉新状态
- 文件用 `.ts` 而非 `.tsx`：内容没有 JSX，放 `.tsx` 会触发 react-refresh 警告
- 倒计时用本地计算（`next_retry_at` 一次性写入，轮询只会给 API 添负载），
  WS `run_retrying` 到达时自然重新起算
- 重试历史卡片只在 `retry_history.length > 0` 时渲染；「尝试次数」MetaTile 仅在
  `attempt > 1` 时出现，避免给绝大多数从未重试的运行加噪声

---

## Phase 4：验收与合并

- [X] T034 Write `deploy/verify/s7_run_retry.py`: 真实栈验收 —— ① 注入一个必然瞬时失败的 LLM 端点（mock 网关前两次连接拒绝、第三次成功）→ 断言 Run 自动重试后 completed 且 attempt=3；② 并发触发同一次重试 → 断言执行一次；③ 成本累计与熔断。
- [X] T035 Update `README.md`: 「已知限制」加一条（重试策略默认值与成本口径变更）；页面/API 若有状态枚举说明则同步。
- [X] T036 Update `docs/architecture.md`: 执行状态机图加 `retrying` 分支；§5 执行引擎补一段重试设计要点（引用 research）。
- [X] T037 Update `specs/006-run-retry/quickstart.md`: 验收记录 + 实测数据。
- [X] T038 Run 全量门禁: `pytest`（后端全量）+ 前端五件套 + 真实栈脚本；记录结果到 quickstart。
- [X] T039 Merge `feat/006-run-retry` into `master`（fast-forward），删除分支，`git status` 确认干净（宪法：无密钥/临时文件）。

---

## 依赖与并行

```
T006/T007 → T008（纯函数：先红后绿）
T009 → T010 → T011（状态机先改，迁移跟进）
T012..T018 → T019（集成测试先红，实现后绿）
T020/T021/T022 与 T019 可并行（配置/审计/事件互不依赖）
T023 → T024（成本口径先改，测试跟上）
T026/T027 与 T019 可并行（schema 独立）
T028..T033 依赖 contracts §3 的字段定义，与后端实现可并行（前端用 mock）
T034..T039 依赖全部前置
```

## 风险

| 风险 | 缓解 |
|---|---|
| PG `ALTER TYPE ADD VALUE` 在 Alembic 事务内失败 | T011 用 `autocommit_block`；本地有数据的开发库 + CI 空库双重验证 |
| 重试消息在终态后到点造成重复执行 | T017 集成测试固化状态校验防线 |
| 累计成本使老任务更易熔断 | spec FR-014 的预期行为；T035 在已知限制明示 |
| 前端把 `retrying` 当失败展示 | T028/T032 专用色调 + vitest 断言 |


---

## Phase 4 实测记录（2026-11-18）

| 项 | 结果 |
|---|---|
| `deploy/verify/s7_run_retry.py` | **4/4 通过**（A 抖动后恢复 / B 耗尽终态 / C 成本熔断 / D retrying 可见） |
| 后端全量 `pytest` | 1287 passed, 6 subtests |
| `ruff check` / `format --check` / `mypy app` | 全绿 |
| 前端五件套 | 全绿；vitest 115 passed / 21 files |
| 文档同步 | README（配置表/已知限制/状态机图）、architecture（重试三要点）、quickstart |

**实测发现并已固化**：
1. LiteLLM 自带 `num_retries=1`，会在一次 run 级尝试内先消耗若干次网关失败，
   所以场景 A 只有 1 次 run 级重试。验收断言因此不断言 attempt 精确值。
2. mock LLM 的失败计数器跨请求累积，重复跑脚本会「预算已用完直接成功」，
   看起来像重试失效。已加 `GET /__reset`，脚本 setup() 先重置。
3. **真实缺口**：最初只把连接类异常判为瞬时，mock 返回 500 时 litellm 抛
   `InternalServerError` 被当成业务类直接终态——spec FR-003 明确要求 5xx 可重试。
   已补 `TRANSIENT_LITELLM_ERRORS`（5xx/连接/超时/限流）并加 11 例单测。

**部署踩坑（已记入 quickstart「怎么重跑」）**：跑 pytest 时 export 的 `DATABASE_URL`
会污染 compose 的变量替换，导致 migrate 容器连到测试库的 127.0.0.1 而失败。
部署前用 `env -u DATABASE_URL` 清掉。
