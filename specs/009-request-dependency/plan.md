# Implementation Plan: 009 需求依赖编排

**Date**: 2026-11-18 | **Branch**: `feat/009-request-dependency` | **Spec**: `specs/009-request-dependency/spec.md`

## Technical Context

- 后端：FastAPI + SQLAlchemy 2.0 async + Alembic（迁移 **0013**）+ Celery + Redis；PostgreSQL 16。
- 前端：React 18 + TS + Vite + Tailwind + dnd-kit + TanStack Query + Zustand。
- 新增依赖：**零**（BFS 用标准库 collections.deque；其余全复用既有模式）。
- 涉及既有文件（全部为受控增量）：
  - `models/request_dependency.py`（新）、`models/request.py`（relationship，无新列）
  - `services/dependency_service.py`（新：边 CRUD + BFS + 门禁查询 + 反查）
  - `services/assignment_service.py`（assign 插门禁一步）、`services/request_service.py`（详情派生 + settle 通知）、`services/schedule_service.py`（建单不分配）
  - `api/v1/requests.py`（边端点挂 router）、`schemas/request.py`（派生字段）
  - 前端：`api/requests.ts`、`DispatchPanel`（依赖区块）、`RequestCard`（徽章）、`BoardPage`（blocked 透传）
- 验收：pytest（真实 PG）+ vitest + 真实栈验收脚本 `deploy/verify/s10_request_dependency.py`。

## Constitution Check

| # | 宪法原则（v1.1.0） | 符合性 | 结论 |
|---|---|---|---|
| 1 | I. SDD+TDD：先 spec 再实现 | ✅ | 九件套先于代码，本 plan 后于它们 |
| 2 | I. Red-Green-Refactor 强制 | ✅ | P2/P3/P4 均测试任务先、实现任务后 |
| 3 | I. 测试未过不标记完成 | ✅ | 每 Phase 跑门禁后才回勾 |
| 4 | II. spec 先行，四步走 | ✅ | 本目录九件套 |
| 5 | II. FR 必有 Given/When/Then 锚点 | ✅ | FR-001~013 挂 US 场景；D-1~D-12 可执行 |
| 6 | II. 范围显式写「不含」 | ✅ | FR-013 + data-model §7 双重钉住（无 blocked 态/无拓扑/无级联） |
| 7 | III. 完整系统非 MVP | ✅ | 边 CRUD/门禁/通知/徽章/审计齐备 |
| 8 | IV. 每 Phase 质量门禁 | ✅ | 每 Phase ruff+mypy+pytest；P4 前端五件套；P5 s10 + 全回归 |
| 9 | 技术栈零新增 | ✅ | BFS 标准库；无新包 |
| 10 | 前端栈不破 | ✅ | 全用既有组件/查询模式 |
| 11 | PG16 + Redis + S3 不变 | ✅ | 仅加表加索引 |
| 12 | LLM 经 LiteLLM | ✅ | 无 LLM 调用 |
| 13 | 自研编排器禁 LangGraph | ✅ | 不碰执行引擎 |
| 14 | 沙箱隔离 | ✅ | 无代码执行 |
| 15 | API RT < 100ms | ✅ | 门禁一次索引查询；SC-003 实测 assign RT |
| 16 | WebSocket 推送 | ✅ | 不新增 WS 频道；门禁/通知走既有错误链路与通知表 |
| 17 | 部署 Compose + Caddy | ✅ | 仅迁移；deploy.sh 纪律不变 |
| 18 | JWT + RBAC 三角色 | ✅ | viewer 403 / owner-or-assignee 谓词 / 404 纪律 |
| 19 | 凭证加密 | ✅ | 无凭证字段 |
| 20 | 脚本只跑沙箱 | ✅ | 无脚本执行 |

**违例登记（Complexity Tracking）**：无。唯一「看起来更简单」的方案
（门禁错误复用 assign 既有 AppError 路径）被 R-07 用实测否掉。

## Phase 拆分（每 Phase 一次提交，纯增量先行）

| Phase | 内容 | 任务 | 提交点 |
|---|---|---|---|
| P2 数据模型 | 模型 + 迁移 0013 + schema 断言测试（含 CHECK/UNIQUE/索引/CASCADE） | T001–T006 | `feat(db): 009 request_dependencies 表` |
| P3a 纯增量 | dependency_service（边 CRUD + BFS + 门禁查询）+ 边端点 + 审计 | T007–T012 | `feat(dependency): 009 依赖边 CRUD` |
| P3b 改既有 | assign 插门禁 + 详情/列表派生 + settle 通知 + 调度建单不分配 | T013–T018 | `feat(assign): 009 硬门禁与解锁通知` |
| P4 前端 | api 层 + 详情依赖区块 + 卡片徽章 + 铃铛类型 + blocked 透传 | T019–T024 | `feat(ui): 009 依赖前端` |
| P5 验收 | s10 脚本 + 全回归 + 文档收尾 + 合并 | T025–T029 | 每项一次提交 |

并行组：P3a 内 service 单测（BFS 纯函数）与端点测试可并行；
P4 内详情区块与卡片徽章无相互依赖。

## 风险与回滚

- **assign 热路径加查询**：一次带索引的半连接；SC-003 用真实栈计时兜底
  （超过 100ms P95 就把门禁查询挪进行锁内或加覆盖索引）。
- **settle 加通知**：settle 在 worker 的 finally 里执行，加的是同事务一次
  反查 + N 条 insert；幂等由 settle 的 running 门禁保证（R-02）。
  worker 崩在通知插入前 → 整事务回滚 → 需求状态不收敛 → 下个 Run 终态
  时重试（既有 settle 语义，无新失败模式）。
- **调度路径变更**：只加「门禁未满足则跳过分配」一支，成功路径逐行不动；
  s5 回归（004 调度脚本）兜底。
- **迁移 0013**：纯加表，向后兼容；downgrade 丢边数据（特性本身的实体），
  quickstart 记明回滚面。
