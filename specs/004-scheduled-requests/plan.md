# Implementation Plan: 定时调度（按规则自动建单并分配执行）

**Branch**: `feat/004-scheduled-requests` | **Date**: 2026-11-17 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/004-scheduled-requests/spec.md`

## Summary

为工作台补上时间维度的触发器：用户定义一条调度规则（需求模板 + 执行目标 + 周期表达式），
Celery Beat 每分钟扫描到期规则，由调度服务按固定判定顺序处理——到点则**复用既有
`assignment_service.assign()`** 创建需求并分配，走与手工拖拽完全相同的编排链路；
没到点、已错过、前次未结束、目标不可用则各自留下带可读原因的触发记录。

核心取舍已在 spec 中确定并由本计划的技术决策落实：

- **不引入第二套调度设施**，复用既有 Beat（R-01）。
- **到期判定用「扫描 + 每规则行锁」**，不为每条规则建 Celery 周期任务（R-02）。
- **幂等靠数据库唯一约束** `(schedule_id, planned_at)`，不靠 Redis 锁（R-03）。
- **错过合并成一条记录且不补跑**，宽限期 90 秒（R-04）。
- **原子性复用 `assign()` 自己的 commit**，先 `flush` 需求再调它（R-05）。
- **周期表达式直接用 Celery `crontab`**，并显式声明「日期∩星期」语义（R-07）。

本计划不实现 002/003 的任何未完成项；三者已完成（见各自 `tasks.md` 全勾选），
本特性只消费它们已建立的分配链路、配额熔断、审计与需求状态机。

## Technical Context

**Language/Version**: Python 3.12+ backend（本机 venv 实测 3.13.15）；TypeScript/React 18 frontend

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic, Celery 5.7.0（含 `celery.schedules.crontab`）, Redis, React/Vite/Tailwind/TanStack Query/Zustand。**不新增依赖**（R-07：不引入 croniter）。

**Storage**: PostgreSQL 16（`schedules`、`schedule_triggers` 两张新表 + `requests` 一个可空外键）；Redis/Celery 仅作既有队列，**不作调度状态源**。

**Testing**: pytest（unit + integration，真实 PostgreSQL 16）；frontend Vitest + lint + typecheck + build；真实栈端到端验证脚本放 `deploy/verify/`。

**Target Platform**: Linux Docker Compose；既有 `beat` 单实例服务承载扫描任务。

**Project Type**: 全栈 Web 服务（FastAPI API + Celery Worker/Beat + React SPA）。

**Performance Goals**:

- 扫描本身可忽略：实测 `crontab` 解析 + 计算下次触发 **0.015 ms/次**，纯计算 **0.003 ms/次**；
  200 条规则的到期判定约 3 ms 计算开销，远小于一次数据库往返。
- 调度处理**不得**拖慢核心接口：所有建单/分配都在 Worker 侧，API 层只做 CRUD。
- SC-002 触发偏差 ≤90 秒（扫描周期 60 秒 + 一次处理余量）。
- SC-006 沿用 002/003 已建立的 P95 归因口径（含 150ms 绝对上限），不另立阈值。

**Constraints**:

- **不新增进程**：不引入 APScheduler、不引入第二个 Beat、不引入系统 cron。
- **不新增依赖**：不引入 croniter / django-celery-beat。
- **不改 `assign()` 的事务边界**：调度复用它的 commit（R-05）。
- **不改 `requests` 既有列语义**：只加一个可空 `source_schedule_id`。
- **不引入调度专属状态**：需求走既有状态机。
- **不补跑**：错过只记录（FR-014/015）。
- 跨时区求值统一按规则声明的 `timezone`（默认 `Asia/Shanghai`，与 `celery_app.conf.timezone` 一致）。

**Scale/Scope**: 单工作台部署；规则量级按数百条设计（单次扫描 200 条计算 3 ms）；
触发历史按 500 条/调度 + 90 天保留；SC-006 验收 20 条同时到期。
不涉及多租户、不涉及分布式选主。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 宪法条款 | 结论 | 证据 / 计划中的应对 |
|---|---|---|
| I. SDD + TDD（NON-NEGOTIABLE） | PASS | spec → plan → tasks → implement 顺序执行；每个 Phase 以失败测试开头（见 tasks 的 Tests first 分组）；未通过测试不得标记完成 |
| II. 规格先行，spec 不泄漏 HOW | PASS | `spec.md` 已先于本计划提交（`bb27ac8`）；技术选型全部收在本计划与 research |
| II. 每条 FR 有可判定锚点 | PASS | 25 条 FR 的锚点在 `checklists/requirements.md` 的「FR 锚点核对」表中逐条列出 |
| II. 范围边界显式写「不含」 | PASS | spec 中 8 条「不含」，每条附依据 |
| II. 未决事项 ≤3 且带标记 | PASS | 0 处 `[NEEDS CLARIFICATION]`；3 个范围歧义在开工前用问答消解 |
| III. 完整系统，非 MVP | PASS | 审计、权限、历史保留、失败自动停用在同一阶段落地，不留「先上线后补」 |
| III. 每 Phase 可独立上线 | PASS | Phase 1–3 后 US1+US2 即可用（建规则 + 自动执行）；US3–US5 增量上线 |
| III. 生产化要素不后补 | PASS | 审计（FR-022）、保留策略（FR-021）、失败自动停用（FR-017）在 Phase 5/6 落地 |
| IV. 质量门禁 | PASS | 每个 Phase 末跑 ruff/format/mypy/pytest；前端跑 lint/tsc/vitest/build |
| IV. 关键路径须集成测试 | PASS | 触发链路是核心路径，契约中「必备行为用例表」22 行全部要求集成测试 |
| 技术栈：Python/FastAPI/SQLAlchemy/Alembic/Celery/Redis | PASS | 完全沿用；无新框架 |
| 技术栈：禁止引入 LangGraph 等第二套执行引擎 | PASS | 调度只调用既有 `assign()`，不新增执行循环 |
| 技术栈：API 只入队，重活交执行引擎 | PASS | 调度扫描与建单全在 Worker（Celery 任务），API 只做规则 CRUD |
| 技术栈：Docker Compose + Caddy 部署 | PASS | 复用既有 `beat` 服务；不改部署拓扑 |
| 安全：RBAC 三角色 | PASS | viewer 403 / member 仅自己 / admin 可读全部不可写，契约中有完整权限矩阵 |
| 安全：资源归属校验 | PASS | FR-005/006；越权与不存在同响应（404），避免枚举 |
| 安全：成本上限与月配额不可绕过 | PASS | FR-011；调度复用 `assign()` 内部的 `assert_user_quota`，不另开门 |
| 安全：敏感操作审计 | PASS | FR-022；新增 6 个 audit action |
| 安全：日志脱敏 | PASS | 触发记录 `reason` 限定白名单措辞，禁止 UUID/堆栈/表名 |
| Governance：复杂度需论证 | PASS | 见 Complexity Tracking；无违例项 |
| Governance：宪法修订需版本号 | 不适用 | 本特性不触发宪法修订 |

**结论**：无违例，无需 Complexity Tracking 的例外论证（该节仍填写，用于记录四个被否决的
简单方案及其否决理由，因为「更简单的做法为何不行」是评审最常问的问题）。

## Research Decisions

详见 [research.md](research.md)。摘要：

1. **R-01** 复用既有 Celery Beat 加一条每分钟扫描任务，不引入第二套调度设施。
2. **R-02** 到期判定用「扫描 + 每规则行锁」，不为每条规则建周期任务（周期任务是静态配置，与运行时增删改冲突）。
3. **R-03** 幂等靠 `UNIQUE(schedule_id, planned_at)`，不靠 Redis 锁（锁会过期、会 flush，不能作为长期事实）。
4. **R-04** 错过合并记录且不补跑，宽限期 90 秒；宽限期为 0 会把每次正常延迟都记成错过。
5. **R-05** 原子性复用 `assign()` 内部 commit；先 `flush` 需求再调它。**自锁风险已实测排除**。
6. **R-06** 「前次未结束」判定该调度最近一次触发的需求，而非目标 Agent 的忙闲。
7. **R-07** 用 Celery `crontab`（参数顺序 `dow` 在 `dom` 前；`tz` 只能实例赋值），语义为交集。
8. **R-08** 触发历史独立成表，`requests` 只加一个可空外键。
9. **R-09** 触发历史清复用每日维护任务模式（500 条/90 天）。
10. **R-10** 前端新增独立 `/schedules` 页，复用既有 UI 与路由分割方式。

## Project Structure

### Documentation (this feature)

```text
specs/004-scheduled-requests/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── api.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
backend/
├── alembic/versions/0007_schedules.py          # 新表 + requests 外键
├── app/models/
│   ├── schedule.py                             # Schedule, ScheduleTrigger
│   ├── request.py                              # +source_schedule_id（只加一列）
│   └── __init__.py                             # 导出新模型
├── app/schemas/
│   └── schedule.py                             # ScheduleCreate/Update/Out, TriggerOut, 枚举
├── app/services/
│   ├── schedule_service.py                     # 规则 CRUD + 判定顺序 + 触发处理（核心）
│   └── schedule_cron.py                        # crontab 解析/下次时刻/次数计算（纯函数）
├── app/api/v1/
│   └── schedules.py                            # /schedules 端点
├── app/tasks/
│   ├── schedule.py                             # Beat 任务包装 run_due_schedules
│   ├── celery_app.py                           # +beat_schedule 一条
│   └── maintenance.py                          # +触发历史清理
├── app/core/
│   ├── config.py                               # +schedule_* 配置
│   └── audit.py                                # +schedule_* action 常量（如有常量表）
└── tests/
    ├── unit/test_schedule_cron.py               # 纯函数：解析/下次时刻/次数/交集语义
    ├── integration/test_schedules_api.py         # 规则 CRUD + 权限矩阵
    ├── integration/test_schedule_triggers.py     # 触发判定顺序 22 行用例表
    └── integration/test_schedule_isolation.py    # 幂等/隔离/20 并发

frontend/src/
├── api/endpoints.ts                             # +schedule DTO 与请求封装
├── pages/SchedulesPage.tsx                      # 列表 + 新建/编辑 + 历史抽屉
├── components/schedule/
│   ├── ScheduleForm.tsx                         # 表单 + cron 语义提示
│   ├── ScheduleList.tsx
│   └── TriggerHistory.tsx
├── components/layout/AppLayout.tsx              # +导航入口
├── router.tsx                                   # +/schedules（lazy）
└── tests/schedules.test.tsx

deploy/
├── docker-compose.yml                            # 不改（沿用既有 beat）
└── .env.example                                 # +schedule_* 配置示例
```

**Structure Decision**: 在现有 backend/frontend 布局内扩展，不新增服务或进程。
`schedule_cron.py` 刻意与 `schedule_service.py` 分离：前者是**无 IO 的纯函数**
（可对 `crontab` 的边界做穷举单测），后者承担事务与副作用（需要真实数据库）。
这个拆分是为了让「下次触发时间算错」这类最容易出错的逻辑能被低成本穷举验证。

## Implementation Phases

### Phase 1 — Cron 纯函数与配置（无副作用，可独立验证）

- `schedule_cron.py`：`parse(spec, tz)` → 校验并返回可求值对象；`next_after(cron, after)`；
  `count_between(cron, start, end)`（含端点语义见契约）。
- 关键实现约束（写进代码注释）：
  - `crontab` 的**位置参数顺序是 `minute, hour, day_of_week, day_of_month, month_of_year`**，
    按标准 cron 顺序传会解析错（实测抛 `Invalid weekday literal`），必须用关键字参数。
  - `tz` **不是构造参数**（传 `tz=` 抛 `TypeError`），只能对实例赋值。
- 单测覆盖：交集语义（`0 9 1 * 1`）、闰年（`0 0 29 2 *`）、跨周末（`0 9 * * 1-5`）、
  「正好到点」的推进（`09:00` → 次日 `09:00`）、非法表达式（`60 * * * *`、`0 0 31 2 *`）、
  次数计算（5 分钟规则停机 9 分钟 = 2 次、2 小时 = 25 次）。

### Phase 2 — 持久化、模型与 schema

- 迁移 `0007_schedules.py`：两个 enum、两张表、`requests.source_schedule_id`，
  含 `UNIQUE(schedule_id, planned_at)`、`CHECK(not enabled or next_run_at is not null)`、
  部分索引 `WHERE enabled`。`downgrade` 逆序 + 显式 `DROP TYPE`。
- 模型与导出；schema（`ScheduleOut` 含展示用 `target.name/status`）。
- 单测/集成：迁移 `upgrade → downgrade → upgrade` 在**空库**上往返（002/003 已有此惯例）。

### Phase 3 — US1：规则 CRUD 与下次触发时间（P1）

- Tests first：`/schedules` 权限矩阵（含 admin 不可写、越权 404）、6 步校验顺序、
  `next_run_at` 在改 cron/时区/启停后重算、`template`/`target` 变更不重算。
- 实现 service + router + 审计。
- Checkpoint：US1 的 Independent Test 通过（工作日 9 点落在下一工作日；非法表达式被拒）。

### Phase 4 — US2：触发建单与分配（P1，MVP 关键）

- Tests first：契约「必备行为用例表」中 `fired` 相关行 —— 三种目标类型（agent / group+fan_out /
  group+pipeline / group+router）的 Run 结构必须与手工分配**逐字段比对**（SC-003）。
- 实现 `fire_schedule` 的 `fired` 分支：建需求 → `flush` → 调 `assign()`（R-05）。
- 实现 Beat 任务与 `beat_schedule` 注册；`now` 可注入。
- 验证 `assign()` 失败时无孤儿需求（FR-009）。

### Phase 5 — US3：错过、跳过、失败与自动停用（P1）

- Tests first：判定顺序的全部 22 行用例表，重点是**顺序**：
  先判错过（第 3 步）再判忙（第 4 步），否则停机恢复会被误记成 `skipped_busy`。
- 实现 `missed`（合并记录 + 次数实测值）、`skipped_busy`、`failed`（含配额/目标/创建者停用）、
  连续失败自动停用、单规则异常隔离。
- Checkpoint：US3 Independent Test —— 停机 2 小时 → 0 新需求 + 1 条 `missed_count=25`。

### Phase 6 — US4 + US5：历史查询与变更不污染历史（P2）

- Tests first：历史分页/过滤/`counts`、`planned_at DESC, id DESC` 的双排序键、
  `reason` 措辞白名单、修改规则后旧需求不变（SC-009）、删除规则后需求与运行保留。
- 实现触发器历史端点、历史清理任务（500 条/90 天）。

### Phase 7 — 前端

- Tests first（Vitest）：表单校验、cron 语义提示可见、权限（viewer 不可见入口）、
  历史抽屉的四种结果展示与跳转。
- 实现 `/schedules` 页与组件、导航入口、路由 `lazy()`。
- 真实浏览器验证（quickstart 的 UI 场景），不靠"渲染没报错"判定。

### Phase 8 — 端到端验收与文档

- 真实栈：20 条调度同时到期（SC-006），用既有归因口径测核心接口。
- 时钟注入验证停机/错过（quickstart 用 `now` 注入，不靠真的等 2 小时）。
- 迁移往返、全量门禁、`git diff --check`、密钥扫描。
- 更新 `README.md`「已知限制」（移除"定时调度尚未实现"）、`docs/architecture.md`、
  `deploy/README.md`、`specs/004/quickstart.md`。
- **如实记录**：未跑通的项标 ⏳ 并写明原因，不勾选。

## Dependencies and Risks

| 依赖 / 风险 | 应对 |
|---|---|
| `assign()` 内部 commit 使得调用方无法控制事务边界 | R-05：先 `flush` 再调；`assign()` 失败则整体回滚并**另开事务**写 `failed` 记录。必须验证无孤儿需求 |
| 同一会话先 `flush` 再 `FOR UPDATE` 是否自锁 | **已实测排除**（真实 PG16 正常返回） |
| `crontab` 参数顺序与 `tz` 不可传参 | R-07：全部用关键字参数 + 实例赋值，并在代码注释与单测中钉住 |
| 交集语义与用户直觉不符 | spec FR-004 + 前端提示明示；单测钉住 `0 9 1 * 1` 的实际行为 |
| Beat 扫描与 Worker 竞争 CPU | 扫描是纯计算 3 ms + 少量查询；Phase 8 在真实栈观察 beat 容器资源（0.5 CPU / 256MB 限额） |
| 停机恢复后误判「忙」而非「错过」 | 判定顺序固定为「错过优先」；用例表专列该行 |
| 长停机导致记录爆炸 | 合并记录（FR-015）；实测 2 小时 → 1 条而非 25 条 |
| 触发器历史无限增长 | 500 条/90 天保留 + 汇总字段保底 |
| 修订规则时与触发并发 | 触发侧对规则行 `FOR UPDATE`，读到的是单一完整版本（US5 场景 2） |
| 调度绕过配额/成本 | 复用 `assign()` 内的 `assert_user_quota`；`failed` 分支不重试 |
| admin 改他人调度导致责任错位 | 契约定为 admin 只读；写操作一律 403 |

## Complexity Tracking

无宪法违例。本节记录**被否决的"更简单"方案**，因为评审最常问的就是这个：

| 更简单的方案 | 为何不行 |
|---|---|
| 只建单不分配（少写一半逻辑） | 会退化成「自动生成待办」，用户仍要手工拖拽；SC-003 消失，特性价值不成立。用户已在开工前明确否决 |
| 为每条规则建一个 Celery 周期任务 | Beat 的 `beat_schedule` 是**进程启动时读入的静态配置**；运行时增删规则必须重启或在进程间同步调度表，并处理旧任务残留 |
| 用 Redis 锁做「同计划时刻只建一张单」 | 锁会过期（TTL）、会被 flush，不能作为长期事实；唯一约束是数据库级硬保证 |
| 错过的时刻全部补跑 | 停机 2 小时、每 5 分钟 → 恢复瞬间 24 个请求涌入，打爆并发与月配额；用户也无法分辨哪些真需要 |
| 每错过一个时刻写一条记录 | 一天停机、每分钟规则 → 1440 条记录，直接违反 FR-015 |
| 先提交需求再调 `assign()` | `assign()` 失败（目标归档/配额耗尽）会留下永远停在 `created` 的孤儿卡片，违反 FR-009 |
| 自建「建 Run + 入队」而不复用 `assign()` | 会绕开授权快照冻结、策略展开、配额熔断，违反 FR-008 与 SC-003 |
| 引入 APScheduler / croniter / django-celery-beat | 分别引入第二套定时器、多余依赖、异技术栈；均为零新增能力付出长期维护成本 |

## Post-Design Constitution Check

| Gate | Result | Evidence |
|---|---|---|
| Spec precedes implementation | PASS | spec 提交于 `bb27ac8`，早于本计划与任何源码改动 |
| All FRs have implementation/test anchors | PASS | 契约「必备行为用例表」22 行 + `checklists/requirements.md` 的 FR→锚点映射表 |
| TDD and quality gates | PASS | 8 个 Phase 均以「Tests first」开头；Phase 8 是全量门禁 + 真实栈验收 |
| Security and production readiness | PASS | 权限矩阵、越权 404、配额复用、审计 6 个 action、reason 措辞白名单、保留策略 |
| Existing architecture preserved | PASS | 复用 Beat / `assign()` / 需求状态机 / 审计；不新增进程、不新增依赖、不改部署拓扑 |
| Scope bounded | PASS | 不做一次性任务、不做调度串联、不做分布式选主、不加附件与 deadline |
| Complexity justified | PASS | 见上表；每个被否决的简单方案都给出具体失效机制 |
| 诚实记录 | PASS | research 中 3 项「未验证」明确列出，Phase 8 要求不得勾选未验证项 |

计划已可进入 `/speckit-tasks`。
