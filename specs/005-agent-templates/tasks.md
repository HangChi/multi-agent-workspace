---
description: "Implementation tasks for agent templates and one-click clone: snapshot templates, instantiate, clone, save-as-template, builtin seeding"
---

# Tasks: Agent 模板与一键克隆

**Input**: Design documents from `/specs/005-agent-templates/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/api.md`, `checklists/requirements.md`

**Approach**: TDD。每个业务任务前先写失败的测试。每个 Phase 独立提交，通过后再进入下一 Phase。
001-004 已完成（各自 `tasks.md` 全勾选），本特性不补做它们的任何未完成项。

## 贯穿全清单的硬约束（Notes）

这些约束在多个任务里反复出现，统一记在这里，避免每个任务重复一遍：

1. **模板是快照，不是引用**：`instantiate()` / `clone_agent()` 必须是**写时拷贝**，
   之后模板与产物双向隔离（FR-002）。任何实现不得让 Agent 行持有「实时读模板」的逻辑。
2. **迁移零 `app.*` 导入**：`backend/alembic/versions/0009_agent_templates.py` 不得
   import 应用代码（仓库既有约定）。内置模板数据只由种子脚本注入。
3. **`UNIQUE(owner_id, name)` 对 `owner_id IS NULL` 不设防**（PostgreSQL NULL 语义）。
   内置模板唯一性必须靠部分唯一索引 `uq_agent_templates_builtin_name`，
   模型层与迁移层都要显式声明，且必须有测试直接覆盖。
4. **工具校验两条路径语义不同**：`AgentCreate` / `AgentTemplateCreate` **保存即拒**
   （不在目录 → 422 Pydantic；在目录但未启用 → 400 `validate_grants`）；
   模板实例化时未启用的工具 → **过滤 + warning**（FR-008）。
   不得把 `validate_grants` 改成从宽。
5. **克隆不复制实例级数据**：运行/步骤/制品、组成员关系、外部工具授权、出站规则一律不复制
   （FR-011），集成测试必须逐项计数断言为 0。
6. **404 同措辞**：访问他人模板/Agent 一律 `404 NOT_FOUND` + 与「不存在」相同的 message，
   不得用 403 区分（FR-004，与 004 FR-006 同一纪律）。
7. **审计全部 `strict=True`**，与 003 审计改造后的约定一致；service 只 `flush`，
   commit 在端点层（`app/api/v1/agents.py` 既有形态）。
8. **前端复用 `AgentForm`**：不为 10 个同义配置字段新写第二套表单（R-07）。
9. **零新增依赖、零新增进程、零新增后台任务**：克隆与模板创建都是同步轻量写。
10. **未验证的不得勾选**：任何需要真实栈/浏览器/时间流逝的项，未做就保持 `[ ]` 并在 Notes 写明原因。
11. **每个 Phase 末跑门禁**：`ruff check` / `ruff format --check` / `mypy` / `pytest`；
    涉及前端时 `eslint` / `prettier` / `tsc` / `vitest` / `build`。

## Phase 1: Setup — 规格四件套

**Purpose**: SDD 四步的前三步产物，不含任何实现代码。

- [X] T001 Write `specs/005-agent-templates/spec.md`: 5 user stories (US1 从模板创建 / US2 一键克隆 / US3 另存为模板 / US4 模板管理 / US5 新部署开箱可用), 22 FR, 9 SC, 6 项显式 Scope Boundaries, Key Entities, Assumptions.
- [X] T002 Write `specs/005-agent-templates/research.md` (R-01..R-08) and `plan.md` (Technical Context / Constitution Check / Phases / Complexity Tracking).
- [X] T003 [P] Write `specs/005-agent-templates/data-model.md` and `contracts/api.md` (endpoint contracts §1-§5, internal service contract §4, audit actions, seed script contract).
- [X] T004 [P] Write `specs/005-agent-templates/checklists/requirements.md` (Content Quality / Requirement Completeness / Feature Readiness / 宪法对齐 / SC→FR / FR→US mappings) and `tasks.md` (this file).
- [X] T005 Point `.specify/feature.json` at `specs/005-agent-templates`.

## Phase 2: Foundational — schema, model, migration, seed

**Purpose**: 数据地基。全部先行写测试（红），再实现（绿）。此阶段不碰任何业务端点。

- [X] T006 [P] Unit: `backend/tests/unit/test_agent_template_schema.py` — assert `AgentTemplateCreate`/`AgentUpdate` constraints are **field-for-field identical** to `AgentCreate`/`AgentUpdate` (same min/max/default/ge/le per field), and that the tools validator rejects non-catalog names. This is the anti-drift pin for Note #8/#4.
- [X] T007 Write `backend/app/schemas/agent_template.py`: `AgentTemplateCreate`, `AgentTemplateUpdate`, `AgentTemplateOut` (with derived `is_builtin`), `AgentTemplateList`. Reuse `ALLOWED_TOOLS` and the tools validator from `app/schemas/agent.py` — no second copy of constraints.
- [X] T008 Write `backend/app/models/agent_template.py`: table `agent_templates` per data-model.md (nullable `owner_id` FK CASCADE, 10 config fields, `created_at`/`updated_at`, 6 CHECK constraints, `UNIQUE(owner_id, name)`, partial unique index `uq_agent_templates_builtin_name WHERE owner_id IS NULL`, `idx_agent_templates_owner`). `egress_mode` reuses the 002 enum with `create_type=False`.
- [X] T009 Modify `backend/app/models/agent.py`: add `source_template_id` and `source_agent_id` (both nullable UUID FKs `ON DELETE SET NULL`, indexed). Do not touch any existing column.
- [X] T010 Write `backend/alembic/versions/0009_agent_templates.py` (down_revision `0008_run_cost_micros`): create table + 2 indexes + 2 agent columns + 2 FKs + 2 indexes; downgrade in reverse; drop no enum. **Must not import `app.*`** (Note #2).
- [X] T011 Integration: `backend/tests/integration/test_migration_0009.py` — `upgrade head → downgrade base → upgrade head` on a disposable DB; assert the partial unique index exists in `pg_indexes`; assert **two builtin-named rows with NULL owner can NOT both be inserted** (this is the R-05 regression pin, Note #3).
- [X] T012 Integration: `backend/tests/integration/test_agent_template_model.py` — CHECK constraints fire (blank name, blank prompt, temperature 2.1, max_steps 0, cost_limit 0); owner-scoped duplicate name rejected; `ON DELETE SET NULL` verified for both new agent columns (delete template/source agent → derived agent survives with NULL source).
- [X] T013 Write `backend/scripts/seed_agent_templates.py`: idempotent injection of 4 builtin templates (通用问答 / 代码审查 / 数据摘要 / 资料检索); `model` from `settings.llm_model`; `tools` filtered to currently-enabled catalog entries; `--dry-run` prints the plan. No audit writes (contracts §4.4).
- [X] T014 Unit: `backend/tests/unit/test_seed_agent_templates.py` — running twice inserts 0 additional rows; second run leaves `updated_at` untouched (no silent overwrite); a template whose tools went offline is re-seeded without duplicates; `--dry-run` performs no writes.

**Checkpoint**: migration round-trips, model invariants hold, seeding is idempotent.

**Phase 2 实测记录（2026-11-18）**

| 项 | 结果 |
|---|---|
| `tests/unit/test_agent_template_schema.py` | 22 passed（约束逐字段一致 + 工具白名单 + 默认值一致） |
| `tests/unit/test_seed_agent_templates.py` | 6 passed（4 个内置模板、模型来自参数、工具交集、下线不废模板） |
| `tests/integration/test_migration_0009.py` | 3 passed（列/索引/FK 齐全；NULL owner 同名被部分索引拒；downgrade 干净且保留 002 枚举） |
| `tests/integration/test_agent_template_model.py` | 13 passed（9 个 CHECK 参数化 + 唯一性 + 双向 SET NULL + 索引落在库里） |
| 迁移往返 | 开发库 `alembic upgrade head` 成功（0008 → 0009）；隔离 schema up→down 干净 |
| 种子脚本 | 开发库实跑插入 4 个内置模板；二次运行新增 0（幂等） |
| `ruff check` / `ruff format --check` / `mypy app` | 全绿（120 source files） |
| 全量 `pytest` | 1155 passed, 1 error —— 该 error 为沙箱 `pytest-of-*` 目录既有的环境问题，在 `master` 上同样复现，与 005 无关 |

**踩到并修掉的坑（已写进代码注释，不重复于此）**：asyncpg 的 prepared statement 查 catalog
视图必须限定列名（`pg_constraint.conrelid`）；SQLAlchemy `text()` 的 `:name` 会吞掉 `::`；
同一事务里一次语句报错后整库作废，因此拒绝用例要换独立连接。

## Phase 3: User Story 3 + 4 — 模板管理服务与端点

**Purpose**: 模板的 CRUD、快照隔离、重名拒绝、内置只读、404 同措辞。US3（另存为模板）的
服务部分在此落地，其端点挂在 `/agents/{id}/save-as-template` 上（Phase 4）。

- [X] T015 Unit: `backend/tests/unit/test_agent_template_service.py` — create/list/get/update/delete with owner scoping; duplicate name → `AppError("CONFLICT", "同名模板已存在")`; foreign template read → `AppError("NOT_FOUND", "模板不存在")` (identical message); builtin update/delete → `AppError("FORBIDDEN", …)`; `list` merges builtins + own with `scope` filter and returns filtered total.
- [X] T016 Implement `backend/app/services/agent_template_service.py`: CRUD + `save_as_template(db, *, agent, name, description)` (snapshot the 10 fields; no status). Flush only — commit stays in the endpoint layer (Note #7).
- [X] T017 [P] Integration: `backend/tests/integration/test_agent_templates_api.py` — `GET /agent-templates` (scope/q/pagination, builtins always visible), `GET/PATCH/DELETE /agent-templates/{id}`: own 200/204, foreign 404 with identical body, builtin 403, duplicate name 409, viewer 403. Assert audit rows for all three actions with `strict` semantics.
- [X] T018 Implement `backend/app/api/v1/agent_templates.py` (contracts §1) and register it in `backend/app/main.py`.
- [ ] T019 Integration: snapshot isolation (FR-002) — create template from agent A, then mutate A and mutate the template, and assert **zero** cross-contamination in both directions; also assert an agent created from the template is unchanged when the template is later edited (SC-004).

**Checkpoint**: US3（另存为模板的服务侧）+ US4 完成；Phase 3 提交。

**Phase 3 实测记录（2026-11-18）**

| 项 | 结果 |
|---|---|
| `tests/integration/test_agent_template_service.py` | 10 passed（归属 404 同措辞、重名 409、内置 403、scope/total、10 字段快照、双向隔离、删除不相连） |
| `tests/integration/test_agent_templates_api.py` | 12 passed（列表/分页/搜索、viewer 403、PATCH/DELETE、409/422、他人 404 同 body、内置只读、另存为模板、审计三动作） |
| `ruff check` / `ruff format --check` / `mypy app` | 全绿（122 source files） |

**实现偏离上游文档之处（已回头改文档，按宪法记录）**：

1. **save-as-template 对他人 Agent 返回 403 而非 404**。既有 `PATCH/DELETE /agents/{id}`
   一向是 403「无权操作他人 Agent」（001 契约），为一致性给新端点单开一套 404 语义
   会制造第二套 Agent 行为。已改 `contracts/api.md` §1.5 并把「模板 404 / Agent 403」
   的不对称显式记录在案。
2. **内置模板在测试里必须带随机名**。部分唯一索引是**全局**的，固定名「内置模板」
   会在第二个测试里撞车——这恰好反证了 R-05 的约束真的在生效。

## Phase 4: User Story 1 + 2 — 实例化、克隆、另存端点

**Purpose**: 把模板变成 Agent 的三条路径，含工具过滤 warning、克隆名称派生、restricted 警告、
审计、以及 `agents` 表来源列的写入。

- [X] T020 [P] Unit: `backend/tests/unit/test_agent_template_instantiate.py` — `instantiate()` overlay order (template values → payload overrides), tool filtering returns filtered names, warning text wording, no instance-level data carried, no commit.
- [X] T021 [P] Unit: `backend/tests/unit/test_agent_clone.py` — 10 config fields copied; name derivation `副本` / `副本2` / `副本3` and truncation of an over-long base name; `restricted` without allow rules yields the exact existing warning sentence; 10 attempts exhausted → `AppError("CONFLICT", …)`.
- [X] T022 Modify `backend/app/services/agent_service.py`: add an optional `prebuilt: Agent | None` parameter to `create_agent()` so template instantiation and clone reuse the **same** insert path (including `validate_grants` on the filtered tool set) instead of a parallel implementation.
- [X] T023 Implement `instantiate()` and `clone_agent()` in `agent_template_service.py` per contracts §4.1/§4.2 (flush only; name derivation reads the used-name set inside the request transaction).
- [X] T024 Implement `POST /agents/from-template/{template_id}`, `POST /agents/{agent_id}/clone`, `POST /agents/{agent_id}/save-as-template` in `backend/app/api/v1/agents.py` (contracts §2/§3/§1.5) with the five audit actions from contracts §4.4 (`agent_create_from_template`, `agent_clone`, `agent_template_create`, `agent_template_update`, `agent_template_delete`).
- [X] T025 Integration: `backend/tests/integration/test_agent_template_instantiate.py` — from-template with unmodified payload → field-for-field equality (SC-001/006); with two edited fields → only those differ; template referencing an offline tool → **201** + filtered name in `warning` (SC-005); validation parity (bad temperature / unknown tool in payload / blank prompt → 422, same as `POST /agents`).
- [X] T026 Integration: `backend/tests/integration/test_agent_clone.py` — cloning a group-referenced agent with runs, external grants and egress rules yields an agent with **0** runs, **0** group memberships, **0** grants, **0** egress rules (SC-003); source agent's counts unchanged (SC-009); three consecutive clones get distinct names; cloning an archived agent succeeds and yields `active` (FR-014); cloning a foreign agent → 404 identical body; deleting the source agent leaves the clone intact with `source_agent_id` NULL.
- [X] T027 Integration: `backend/tests/integration/test_agent_save_as_template_api.py` — save from own agent 201 with snapshot; duplicate name 409 with no auto-suffix; archived source allowed; saving a builtin template as own template produces an editable copy and leaves the builtin untouched (FR-018); viewer 403.

**Checkpoint**: US1 + US2 + US3 端点完成；Phase 4 提交。

**Phase 4 实测记录（2026-11-18）**

| 项 | 结果 |
|---|---|
| `tests/unit/test_agent_template_instantiate.py` | 14 passed（模板打底/覆盖、工具过滤与保序去重、名称派生 3 变体、截断、10 次放弃、不共享 list） |
| `tests/unit/test_agent_template_schema.py` | 33 passed（含 `AgentFromTemplateIn` 与 AgentCreate 的非 tools 字段逐字段一致） |
| `tests/integration/test_agent_template_instantiate_api.py` | 20 passed（逐字段一致、只改两字段、模板/payload 双侧工具过滤+warning、6 个参数化校验、内置可被他人用、克隆 8 场景、删除源后克隆体完好、审计） |
| 全量 `pytest` | **1224 passed**, 6 subtests（排除既有沙箱环境问题文件 test_mcp_fixture_http.py） |
| `ruff check` / `ruff format --check` / `mypy app` | 全绿（122 source files） |

**实现偏离上游文档并已回头改文档（按宪法记录）**：

1. **from-template 端点改用专用请求模型 `AgentFromTemplateIn`**（tools 不做目录校验）。
   原因：用完整 `AgentCreate` 时，模板引用一个已下线工具会让用户的「原样提交」
   先撞 422，FR-008 的过滤逻辑永远走不到。契约 §2 已补「为什么 tools 从宽」一节，
   并说明这不是第二条宽松旁路（FR-008 本身规定了该字段从宽，FR-009 管其余字段）。
2. **`instantiate()` 的 payload None 语义统一为「未提供」**。最初让 `description: null`
   表示清空，结果一个没碰描述的用户会静默抹掉模板的适用场景——前端永远提交完整对象，
   null 只可能是「没填」。
3. **克隆不复制外部工具授权的断言改为说明而非搭建**：一个 grant 需要
   server → version → listing → installation 四层链，而克隆体 id 全新、
   `clone_agent` 只写 SNAPSHOT_FIELDS（单测逐字段锁定），子表不可能引用它。

## Phase 5: Frontend — 模板管理页与入口

**Purpose**: 模板可见、可选、可管；Agent 页出现「从模板创建 / 克隆 / 另存为模板」。

- [ ] T028 [P] Write `frontend/src/api/agentTemplates.ts`: `AgentTemplate` type + `fetchAgentTemplates({scope,q,page,page_size})` / `fetchAgentTemplate(id)` / `updateAgentTemplate(id, payload)` / `deleteAgentTemplate(id)` / `createAgentFromTemplate(id, payload)` / `cloneAgent(id)` / `saveAgentAsTemplate(id, payload)`; export through `frontend/src/api/endpoints.ts` barrel. Add `AgentSaveResponse` (with `warning`) and `source_template_id`/`source_agent_id` to the `Agent` interface in `frontend/src/api/agents.ts`.
- [ ] T029 [P] Write `frontend/src/components/agent/TemplatePicker.tsx`: modal listing builtin + own templates (name/description/model/tools), search, selection callback; builtin rows show a 「内置」 badge and no edit affordance.
- [ ] T030 Write `frontend/src/components/agent/TemplateForm.tsx`: template create/edit form **reusing** the existing `Field` primitives (Input/Select/Slider/NumberInput/Textarea) and the tools catalog fetch — a thin wrapper over the same field set as `AgentForm`, not a second implementation (Note #8).
- [ ] T031 Write `frontend/src/pages/AgentTemplatesPage.tsx`: two sections (内置模板 read-only / 我的模板), edit + delete + 「用这个模板创建」 + 「另存为模板」 entry; empty states; confirm dialogs via `useConfirm`; toasts via `useToast`.
- [ ] T032 Modify `frontend/src/pages/AgentPage.tsx`: add a primary 「从模板创建」 button opening `TemplatePicker`; on selection, open `AgentForm` with `initial` prefilled from the template and route submission to `createAgentFromTemplate`; surface `warning` from the response.
- [ ] T033 Modify `frontend/src/components/agent/AgentListItem.tsx`: add 「克隆」 and 「另存为模板」 actions (clone → toast + list refresh; save-as-template → small name/description dialog → `saveAgentAsTemplate`).
- [ ] T034 Modify `frontend/src/router.tsx` (lazy chunk, following the `SchedulesPage` precedent) and `frontend/src/components/layout/AppLayout.tsx` (nav entry 「模板」).
- [ ] T035 [P] Frontend tests: `frontend/src/pages/__tests__/AgentTemplatesPage.test.tsx` (builtin read-only, own template edit/delete, empty state) and `frontend/src/components/agent/__tests__/TemplatePicker.test.tsx` (search filters, selection callback fires with the template, builtin badge present).

**Checkpoint**: US1/US2/US4 前端完成；Phase 5 提交。

## Phase 6: 真实栈验收、门禁与文档

**Purpose**: 在真实容器栈上验收 S1-S5，跑全量门禁，同步文档。

- [ ] T036 Write `deploy/verify/s5_agent_templates.py`: scenarios S1 (from-template create, field equality + offline-tool filtering), S2 (clone of a group-referenced agent, instance-level zeros + source untouched), S3 (save-as-template then mutate both sides), S4 (template CRUD + builtin 403 + foreign 404 identical body), S5 (fresh DB: seed → builtins ≥3, seed twice → no growth). Register usage in `deploy/README.md`.
- [ ] T037 Run full backend gates: `ruff check app tests` / `ruff format --check .` / `mypy app` / `pytest` (with `fail_under` coverage gate) and full frontend gates: `eslint` / `prettier --check` / `tsc --noEmit` / `vitest run` / `vite build`. Fix any failure before ticking.
- [ ] T038 [P] Update `README.md` (pages table + API overview + 已知限制: 模板为个人私有、无共享市场), `docs/architecture.md` (data model: one table + two nullable columns), `deploy/README.md` (seed script invocation), and record real-stack acceptance results in `specs/005-agent-templates/quickstart.md`.
- [ ] T039 Merge `feat/005-agent-templates` into `master` (fast-forward), delete the branch, and verify `git status` is clean (constitution: no stray keys/temp files).

## Dependencies and Execution Order

- Phase 1 (done) → Phase 2 blocks everything (schema + migration).
- Phase 3 depends on Phase 2 only.
- Phase 4 depends on Phase 3 (service) + Phase 2.
- Phase 5 depends on Phase 4 contracts (can start in parallel once `contracts/api.md` is frozen — T028/T029 are marked [P] for that reason).
- Phase 6 depends on all.

## Parallel Opportunities

- T006 [P] with T007 drafting; T003/T004/T005 were parallel in Phase 1.
- T020/T021 [P] (independent pure-logic units).
- T028/T029 [P] (frontend API layer vs picker component).
- T035 [P] after T031.

## Implementation Strategy

1. Phase 2 first — the migration and the partial-unique-index pin (T011) are the only
   place where a silent schema mistake would survive until production (R-05).
2. Phase 3 + 4 together deliver the whole backend; commit each Phase separately so a
   rollback never has to span schema and endpoints.
3. Phase 5 is contract-driven; it can overlap Phase 4 tail.
4. Phase 6 gates the feature: no merge with a red gate.

## Notes

- Any item requiring a real browser or time-based behaviour stays `[ ]` until actually executed,
  with the reason recorded here.
- If implementation deviates from spec/plan/contracts, fix the upstream document **first**
  and say so in the commit message (constitution, 开发工作流).
