# Implementation Plan: Agent 模板与一键克隆

**Branch**: `feat/005-agent-templates` | **Date**: 2026-11-18 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/005-agent-templates/spec.md`

## Summary

新增一张 `agent_templates` 表保存 Agent 配置的**快照**，`agents` 表加两个可空来源外键
（`source_template_id` / `source_agent_id`，均 `ON DELETE SET NULL`），
通过四个端点把「复用」落到产品里：**另存为模板**、**从模板创建**（模板值打底 + 请求体覆盖）、
**一键克隆**（拷配置不拷实例级数据）、**模板管理**（增删改查 + 内置只读）。
内置模板由幂等种子脚本按当前 `LLM_MODEL` 注入，不写进迁移。

核心取舍已在 spec 中确定并由本计划的技术决策落实：

- **模板是快照不是引用**，写时拷贝后双向隔离（R-01）。
- **迁移零 `app.*` 导入**，内置模板走种子脚本（R-02）。
- **工具失效从宽**：实例化时过滤 + warning；保存时从严（R-03）。
- **克隆不复制实例级数据**，警告复用 `AgentSaveOut.warning`（R-04）。
- **内置唯一性用部分唯一索引**，不用 `UNIQUE(owner_id, name)` 硬扛 NULL（R-05）。

## Technical Context

**Language/Version**: Python 3.12+ backend（本机 venv 实测 3.13.15）；TypeScript/React 18 frontend

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic, Celery, Redis,
React/Vite/Tailwind/TanStack Query/Zustand。**零新增依赖**。

**Storage**: PostgreSQL 16：1 张新表 `agent_templates` + `agents` 2 个可空列/2 个外键/2 个索引。
复用 002 已建的 `agent_egress_mode` enum（`create_type=False`）。

**Testing**: pytest（unit + integration，真实 PostgreSQL 16）；frontend Vitest + lint +
typecheck + build；真实栈验证脚本放 `deploy/verify/`。

**Target Platform**: Linux Docker Compose（既有部署形态，无新服务、无新进程）。

**Project Type**: 全栈 Web 服务（FastAPI API + Celery Worker + React SPA）。

**Performance Goals**:

- 模板列表与 Agent 列表同阶：单次查询走 `(owner_id, created_at)` 复合索引，
  响应目标与 `GET /agents` 一致（本地实测 `GET /agents` 分页 <10ms 量级）。
- 克隆/从模板创建都是**同步的轻量写**（一次 INSERT + 一次 SELECT 名称集合），
  不触发任何重活，不得引入 Celery 任务（复用 001「API 层只做校验 + 写入」的分层）。
- 不新增任何后台任务；本特性无 Beat 相关工作。

**Constraints**:

- **零新增依赖、零新增进程、零新表索引**（除 data-model 明列的两个）。
- **不改既有 Agent 任何列语义**（只加两个可空列）。
- **不改 `validate_grants` 语义**：手工创建路径保持「白名单外即 422」。
- **不改 `assign()` / 编排 / 调度 / 沙箱 / 外部工具**任何行为。
- **模板路径不得成为绕过校验的旁路**（FR-009/FR-022）。
- **迁移不得导入 `app.*`**（R-02，仓库既有约定已实测成立）。
- 前端**复用 `AgentForm`**，不新写第二套配置表单（R-07）。

**Scale/Scope**: 单工作台部署；模板量级按数百条设计（与 Agent 同阶）；
内置模板固定 4 个；不涉及多租户、共享、市场、导入导出。

## Constitution Check

| 宪法条款 | 本计划如何满足 |
|---|---|
| I. SDD + TDD 双驱动 | spec 已评审通过后才进入本计划；每个 Phase 先写失败测试（红）再实现（绿），tasks.md 逐条对应 |
| II. 规格先行 | spec.md 含 5 个用户故事 / 22 条 FR / 9 条 SC + 显式 Scope Boundaries；无 `[NEEDS CLARIFICATION]`，未决项已进 Assumptions |
| III. 完整系统非 MVP | 审计（FR-019）、资源归属（FR-004/020）、输入校验（FR-009/022）在**本特性内**落地，不留「上线后补」 |
| IV. 质量门禁 | 每 Phase 跑 lint + format + mypy + pytest + 覆盖率门禁；前端 lint/format/typecheck/test/build |
| 技术栈约束 | 全部既有栈，零新增依赖 |
| 安全边界 | 归属校验 + 404 同措辞不泄露存在性 + 审计全覆盖 + 不复制凭证/授权 |
| 开发工作流 | `feat/005-agent-templates` 分支；每 Phase 一次提交；实现偏离上游文档时回头改文档并在提交信息说明 |

## Research Decisions

完整论证见 [research.md](research.md)。摘要：

- **R-01** 快照而非引用 —— 避免「改模板、N 个 Agent 同时变脸」与 004 FR-020 同纪律。
- **R-02** 内置模板走种子脚本 —— 迁移不导入 app 代码 + 模型必须跟随 `LLM_MODEL`。
- **R-03** 实例化时过滤失效工具并 warning —— 不让一次工具下线废掉模板，也不静默改用户意图。
- **R-04** 克隆不复制实例级数据，warning 复用既有字段 —— 避免「显示已授权、跑起来才失败」。
- **R-05** 内置唯一性用部分唯一索引 —— `UNIQUE(owner_id, name)` 对 NULL 完全不设防。
- **R-06** 两个可空来源外键 + SET NULL —— 删模板/删源 Agent 都不带走产物。
- **R-07** 独立模板页 + Agent 页两入口，复用 `AgentForm` —— 不为 10 个同义字段维护两份表单。
- **R-08** 最小索引集 —— 模板量级与 Agent 同阶，额外索引只有写入代价。

## Project Structure

### 新增文件

```
specs/005-agent-templates/            # 本目录（规格四件套）
backend/app/models/agent_template.py        # AgentTemplate 模型
backend/app/schemas/agent_template.py       # AgentTemplateCreate/Update/Out/List
backend/app/services/agent_template_service.py  # 模板 CRUD + 实例化 + 克隆 + 另存
backend/app/api/v1/agent_templates.py       # 4 组端点（§1-§3）
backend/alembic/versions/0009_agent_templates.py
backend/scripts/seed_agent_templates.py     # 幂等内置模板注入
frontend/src/api/agentTemplates.ts          # 模板端点封装 + 类型
frontend/src/pages/AgentTemplatesPage.tsx   # 模板管理页
frontend/src/components/agent/TemplatePicker.tsx     # 「从模板创建」选择弹层
frontend/src/components/agent/TemplateForm.tsx       # 模板编辑表单（复用 Field 组件）
backend/tests/unit/test_agent_template_service.py
backend/tests/integration/test_agent_templates_api.py
backend/tests/integration/test_agent_template_instantiate.py
backend/tests/integration/test_agent_clone.py
frontend/src/pages/__tests__/AgentTemplatesPage.test.tsx
frontend/src/components/agent/__tests__/TemplatePicker.test.tsx
deploy/verify/s5_agent_templates.py         # 真实栈验收（S1-S5 场景）
```

### 修改文件

```
backend/app/main.py                     # 注册 agent_templates_router
backend/app/models/agent.py             # + source_template_id / source_agent_id
backend/app/schemas/agent.py            # + AgentSaveOut 已有的 warning 不变；AgentOut 加两个来源字段
backend/app/api/v1/agents.py            # + from-template / clone / save-as-template 三个端点
backend/app/services/agent_service.py   # create_agent 支持已构造好的 Agent（供实例化复用）
frontend/src/router.tsx                 # + /agent-templates 路由（懒加载分包）
frontend/src/components/layout/AppLayout.tsx  # + 导航项
frontend/src/api/agents.ts              # + AgentSaveResponse（含 warning）/ 三个新调用
frontend/src/api/endpoints.ts           # barrel 再导出
frontend/src/pages/AgentPage.tsx        # + 「从模板创建」按钮
frontend/src/components/agent/AgentListItem.tsx  # + 克隆 / 另存为模板
README.md                               # 页面表 + 已知限制
deploy/README.md                        # 种子脚本登记
docs/architecture.md                    # 数据模型补一张表 + 两个外键
.specify/feature.json                   # 指向 005
```

## Implementation Phases

> 每个 Phase 一次提交；Phase 结束立即更新 tasks.md 勾选状态。
> 顺序原则：**先测试后实现**；**先上游后下游**（schema → 迁移 → 模型 → 服务 → 端点 → 前端）。

### Phase 1 — 规格四件套（T001-T003）
spec / plan+research / data-model + contracts + checklist + tasks。
提交：`docs(spec): 005-agent-templates 功能规格（5 用户故事/22 FR/9 SC）`

### Phase 2 — 地基：schema 与迁移（T004-T009）
`agent_template.py` schema（含与 Agent 的约束一致性单测）→ 模型 → 迁移 0009 →
`agents` 两个来源列 → 迁移 round-trip（up→down→up）→ 种子脚本（含幂等单测）。
提交：`feat(db): agent_templates 表与 Agent 来源列（T004-T009）`

### Phase 3 — 模板服务与端点（T010-T017）
服务层：CRUD + 快照隔离 + 重名拒绝 + 404 同措辞 + 内置只读（全为先写测试）。
端点：§1 五组。审计三动作。
提交：`feat(agents): Agent 模板管理端点与快照隔离（T010-T017）`

### Phase 4 — 实例化：从模板创建与克隆（T018-T024）
`instantiate()` + 工具过滤 warning；`clone_agent()` + 名称派生 + restricted 警告；
`save_as_template()`；`agents` 三个新端点；审计两动作。
提交：`feat(agents): 从模板创建 / 一键克隆 / 另存为模板（T018-T024）`

### Phase 5 — 前端：模板管理页与入口（T025-T032）
`agentTemplates.ts` → `TemplatePicker`（从模板创建）→ `AgentTemplatesPage`（管理）
→ 路由 + 导航 → AgentPage 入口 → AgentListItem 克隆/另存动作 → warning 展示。
提交：`feat(ui): 模板管理页 + 从模板创建 + 克隆入口（T025-T032）`

### Phase 6 — 真实栈验收与门禁（T033-T038）
`deploy/verify/s5_agent_templates.py`（S1-S5 场景跑真实容器栈）→ 全量门禁
→ README / deploy README / architecture 文档同步 → `.specify/feature.json`。
提交：`docs: 同步 005 实际进展与验收记录（T033-T038）`

## Dependencies and Risks

| 风险 | 影响 | 缓解 |
|---|---|---|
| `agent_egress_mode` enum 复用写错（重复 CREATE TYPE） | 迁移失败 | 用 `create_type=False` + round-trip 测试（与 0007 同手法，已被 CI 验证） |
| `UNIQUE(owner_id, name)` 对内置 NULL 不设防 | 种子重复插入重名模板，SC-008 生产才失败 | 部分唯一索引 + 种子幂等单测 + 集成测试直接跑两次种子断言数量不变 |
| `tools` 过滤把用户没注意的下线工具静默去掉 | 用户困惑 | warning 固定措辞 + 集成测试断言覆盖 100%（SC-005） |
| 克隆撞名（并发/连续） | 同名 Agent 或 409 | 名称派生带 2..10 后缀 + 集成测试连续克隆 3 次 |
| 自引用外键 `agents.source_agent_id → agents.id` 删除行为 | 误删克隆体 | `ON DELETE SET NULL` + 集成测试删源 Agent 后克隆体完好 |
| `agents` 表加列影响既有查询性能 | 列表变慢 | 两个新列均可空、无默认值写入、无 NOT NULL 校验开销；加普通索引只为来源反查 |
| 前端第二套表单漂移 | 模板与 Agent 校验不一致 | 复用 `AgentForm` + 约束一致性单测（T005） |
| 内置模板提示词质量差 | 开箱即用变成开箱劝退 | Phase 6 用当前端点实跑 4 个模板各一次，不过关就改文案再跑 |

## Complexity Tracking

本特性无「更简单做法不可行」的额外复杂度，但有两处**必须记录的选择**：

| 选择 | 更简单的替代 | 为什么不用 |
|---|---|---|
| 独立 `agent_templates` 表 | 给 `agents` 加 `is_template` 布尔列，模板就是一类 Agent | 模板无 status/运行/组引用，混表会让看板拖拽目标、组成员选择器、归档/删除语义全部多一个过滤条件；且 Agent 删除有「被组引用 409」规则，模板没有——两类生命周期混在一张表里，每个查询都要记得区分。独立表的代价是两张表的 10 个同义字段，用共享 schema 定义 + 一致性单测抵消。 |
| 两个来源外键 | 一个 `source_type + source_id` 多态字段 | 多态丢外键约束，退化成应用层约定；本处是同一类资源（Agent/模板）的两种来源，两个外键更便宜也更安全。004 用多态是因为 Agent 与 Group 确实是两类不同资源。 |

## Post-Design Constitution Check

- [x] spec 无 HOW 泄漏：无框架/表名/端点细节进入 spec.md（端点与表名只在 data-model/contracts）。
- [x] 每条 FR 有 Given/When/Then 验收场景或可计数判据（见 tasks.md 的映射列）。
- [x] Scope Boundaries 显式排除 6 项，防 plan 阶段膨胀。
- [x] 生产化要素（审计、归属、校验）本特性内落地，无「先上线后补」。
- [x] 零新增依赖、零新增进程。
- [x] 复杂度两项均已论证「为什么更简单的做法不行」。
