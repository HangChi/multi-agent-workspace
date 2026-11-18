# Plan: 007-request-dispatch 实施计划

**Feature**: `007-request-dispatch` | **Branch**: `feat/007-request-dispatch` | **Date**: 2026-11-18
**宪法版本**: 1.1.0（本特性开工时同步修订，见 Sync Impact Report）

## 1. Technical Context

- **后端**：Python 3.12 + FastAPI + SQLAlchemy 2.0 (async) + Alembic + Celery 5 + Redis（宪法 §技术栈，零新增依赖）
- **前端**：React 18 + TypeScript + Vite + TailwindCSS + dnd-kit + TanStack Query + Zustand（零新增依赖）
- **数据库**：PostgreSQL 16（`requests` +3 列、+2 表、枚举 +2 值；迁移编号 `0011_request_dispatch`）
- **实时**：复用既有 Redis Pub/Sub 事件总线，新频道 `request:{id}`（不引入用户级长连接）
- **测试**：pytest（真实 PostgreSQL 16 + Redis 7 service，MinIO docker run）+ vitest；
  真实栈验收脚本 `deploy/verify/s8_request_dispatch.py`
- **通知实时性**：TanStack Query `refetchInterval` 轮询未读数（≤30s），不建用户级 WS

## 2. Constitution Check（逐条核对，宪法 1.1.0）

| 原则/条款 | 符合性 | 结论 |
|---|---|---|
| I. SDD+TDD：先 spec 后实现，业务逻辑先红后绿 | ✅ | spec 已评审确认（本轮交付物）；实现 Phase 全部 TDD，测试先行 |
| I. 提交必过当阶段门禁 | ✅ | 每 Phase 一次提交，提交信息记录覆盖任务编号 |
| II. 规格 WHAT/WHY，不泄漏 HOW | ✅ | spec.md 无端点/表名字段级细节；契约细节全部在 contracts/data-model |
| II. 每条 FR 有可判定验收锚点 | ✅ | 15 条 FR 全部映射到 US 的 Given/When/Then 或 contracts 行为用例表 |
| II. 显式「不含」 | ✅ | spec §2.2 七项各带依据 + §6 六条防膨胀 |
| II. 未决事项 ≤3 且标注 | ✅ | 0 个 `[NEEDS CLARIFICATION]`；全部存疑点以 Assumptions 落定并说明理由 |
| III. 每 Phase 可独立上线 | ✅ | Phase 划分见 §4：Phase 2（模型+状态机）自身可上线（无行为变化）；Phase 3（分派闭环）可上线；Phase 4/5 增量可上线 |
| III. 生产化要素不「先上线后补」 | ✅ | 审计（FR-013/SC-003）与 Phase 3 同提交落地；可观测性复用 003 `/metrics` 与既有结构化日志，新增指标随 Phase 4 |
| IV. lint + 类型 + 测试门禁 | ✅ | ruff/format + mypy + pytest + eslint/prettier/tsc/vitest 全量 |
| IV. 关键路径集成测试 | ✅ | 分派链路（A-*/AC-*/RJ-* 用例表）即「拖拽分配」关键路径的扩展，全量集成覆盖 |
| IV. 覆盖正常/边界/失败/并发/幂等 | ✅ | US6 边界故事 + A-10/AC-5 并发用例 + 幂等（重复 accept/read） |
| IV. 提交前 git status 复查 | ✅ | 每 Phase 提交前执行；密钥扫描 `git grep` |
| §技术栈：后端 Python 栈 | ✅ | 无新框架、无新依赖（通知落库 PG，不引 MQ/推送服务） |
| §技术栈：前端 React 栈 | ✅ | dnd-kit 落点扩展第三类目标；TanStack Query 轮询 |
| §技术栈：PG16/Redis/MinIO | ✅ | 仅 PG  schema 变更 |
| §架构：API 只入队与校验，RT<100ms | ✅ | user 分派分支不建 Run 不入队，受理路径比 agent 分支更短；SC-001 钉住 P95 |
| §架构：WS 推送运行状态 | ✅ | 既有 run 频道不变；新增 request 频道复用同基建（同鉴权同心跳） |
| §安全：JWT + RBAC 三角色 | ✅ | users/options 与分派对 viewer 403；accept 仅当前 assignee |
| §安全：资源归属校验 | ✅ | FR-008「owner 或当前 assignee」，非相关人员 404（与既有纪律同向收紧而非放松） |
| §安全：敏感操作审计 | ✅ | 四个新审计动作集中定义，SC-003 断言 100% |
| §工作流：偏离必改上游文档 | ✅ | 契约变更（scope 默认值、配额入参、既有测试断言收窄）全部在 contracts §7 与 tasks 中逐条列明 |

**无违例**。两处「更简单的做法为何被否决」登记备查（完整论证在 research）：

1. **用户级 WS 广播通知**（更「实时」）被否决：需在线状态/多标签去重/重连补发，
   为铃铛小红点引入长连接运维面，违反简洁高效；轮询 ≤30s 满足 SC-007。
2. **`assigned` 复用表示「已分派给人」**（零枚举变更）被否决：看板按状态筛选会说谎
   （006 R-03 同款纪律），且状态机无处安放集中校验。

## 3. 设计概要

```
拖拽落点三类：Agent / 组 / 员工(user)
                    │
   assign(user) ────┼── 状态机 created|accepted→pending（换人=转交）
                    ├── 历史 request_dispatches（append-only）
                    ├── 通知 notifications（新接收人 + 转交时旧接收人）
                    └── 审计 request_dispatch
                    │
   accept ───────────┼── pending→accepted（仅当前 assignee）+ 通知分派人
   reject ───────────┼── pending|accepted→created（清三列，reason 必填）+ 通知分派人
                    │
   assign(agent/组) ─┼── 既有链路零行为变更；准入状态 +accepted；配额入参改 owner
```

关键不变量（测试逐条锁定）：

- `assignee_id IS NOT NULL ⇔ status ∈ {pending, accepted}`
- 成本/配额恒记 `request.owner_id`（Run 继承同源，双口径不分裂）
- 分派类操作对 `assigned` 及以后一律 409（行级锁 + 状态机双重）

## 4. Phase 划分（每 Phase 一次提交，可独立上线）

| Phase | 内容 | 可独立上线？ |
|---|---|---|
| P1 规格 | spec/plan/research/data-model/contracts/checklists/quickstart/tasks（本轮已完成至 tasks） | — |
| P2 数据模型与状态机 | 迁移 0011 + 模型 + `request_state_machine.py` + 单测（TDD） | ✅ 无行为变化 |
| P3 分派闭环（TDD） | assign user 分支 / accept / reject / 可见性 / 配额口径 + 集成测试 + 审计 | ✅ 核心闭环 |
| P4 通知与人员端点 | users/options + notifications 读写 + 轮询 + `/metrics` 新指标 | ✅ 增量 |
| P5 前端 | 员工拖拽目标 + 收件箱列 + 详情时间线 + 铃铛/通知中心 + 回归 | ✅ 增量 |
| P6 验收合并 | 真实栈 s8 脚本 + 全量门禁 + 文档同步 + ff 合并 | — |

高风险改写与纯增量拆分（skill 经验 9）：P3 内先做「纯增量」（新端点 accept/reject、
user 分支），再单独提交「改既有」（assign 准入状态、配额入参、可见性谓词）——
出问题时「该回滚什么」必须一眼可答。

## 5. 风险

| 风险 | 缓解 |
|---|---|
| `GET /requests` 默认 scope 变更影响既有测试与用户习惯 | contracts §7 列全变更点；P3 逐条更新既有集成测试并在提交信息说明；quickstart 实测回归 |
| 配额入参改 owner 改变既有 429 触发方（操作人→owner） | 集成测试双边断言（SC-005）；契约文档明示；owner 配额超限时分派/执行都拒，与「预算跟需求方走」的产品语义一致 |
| 枚举 ADD VALUE 与同事务使用冲突 | data-model §5：无存量回填，单 migration 安全（PG 12+ 允许事务内 ADD VALUE，仅不能同事务使用） |
| 前端拖拽三类目标后 AssignModal 策略选择对 user 落点无意义 | user 落点跳过策略步骤直接确认（弹窗按 assignee_type 分支渲染） |
| 通知轮询增加看板页 QPS | 轮询仅顶栏铃铛（独立轻查询，部分索引）；看板数据不轮询，靠 WS/既有 invalidate |
| 禁用用户收件箱遗留 pending 需求成「死单」 | contracts §6 US6 场景：owner/经手人可转交给在职员工；R-05 人员列表过滤禁用账号 |
