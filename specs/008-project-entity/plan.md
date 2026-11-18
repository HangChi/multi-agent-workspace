# Implementation Plan: 008 项目实体与需求归属

**Date**: 2026-11-18 | **Branch**: `feat/008-project-entity` | **Spec**: `specs/008-project-entity/spec.md`

## Technical Context

- 后端：FastAPI + SQLAlchemy 2.0 async + Alembic（迁移 **0012**）+ Celery + Redis；PostgreSQL 16。
- 前端：React 18 + TS + Vite + Tailwind + dnd-kit + TanStack Query + Zustand。
- 新增依赖：**零**（项目 CRUD 复用既有 APIRouter/service/Modal/ConfirmDialog 全部模式）。
- 涉及既有文件（全部为增量，无重写）：`models/request.py`（+2 列）、`api/v1/requests.py`
  （+2 参数 + 权限分流）、`services/request_service.py`（校验 + 快照维护）、
  `core/audit.py`（+3 动作常量）、`main.py`（挂路由）、前端 `RequestForm`/`Board`/
  `BoardPage`/`nav.ts`/`api/`。
- 验收：pytest（真实 PG）+ vitest + 真实栈验收脚本 `deploy/verify/s9_project_container.py`。

## Constitution Check

| # | 宪法原则（v1.1.0） | 符合性 | 结论 |
|---|---|---|---|
| 1 | I. SDD+TDD：先 spec 再实现 | ✅ | spec/research/data-model/contracts 已提交，本 plan 后于它们 |
| 2 | I. Red-Green-Refactor 强制 | ✅ | tasks.md 按「测试任务先、实现任务后」成对编排（P2/P3/P4 均如此） |
| 3 | I. 测试未过不标记完成 | ✅ | 每 Phase 跑门禁后才回勾；未实测项留 ⏳ |
| 4 | II. spec 先行，四步走 | ✅ | 本目录九件套 |
| 5 | II. FR 必有 Given/When/Then 锚点 | ✅ | FR-001~012 全部挂在 US 场景上；P-1~P-9 用例表可执行 |
| 6 | II. 范围显式写「不含」 | ✅ | spec FR-011 + data-model §5 双重钉住：无依赖边、无成员表、无统计物化 |
| 7 | III. 完整系统非 MVP | ✅ | CRUD/归档/删除/审计/快照/筛选/深链齐备；缺一个环节（如无归档）才叫 MVP |
| 8 | IV. 每 Phase 质量门禁 | ✅ | 每 Phase 末跑 ruff+mypy+pytest（前端五件套）；Phase 6 跑 s9 + s8 回归 |
| 9 | 技术栈：FastAPI/SQLAlchemy2/Alembic/Celery/Redis | ✅ | 纯用既有栈，零新依赖 |
| 10 | 前端栈：React18/TS/Vite/Tailwind/dnd-kit/TanStack/Zustand | ✅ | 管理页与筛选全用既有组件（Modal/ConfirmDialog/Select） |
| 11 | PG16 + Redis + S3（MinIO） | ✅ | 仅加表加列加索引，不动存储拓扑 |
| 12 | LLM 经 LiteLLM 统一接入 | ✅ | 本特性无 LLM 调用 |
| 13 | 自研编排器，禁 LangGraph 运行时依赖 | ✅ | 不碰执行引擎（FR-011） |
| 14 | 沙箱隔离执行 | ✅ | 无代码执行路径 |
| 15 | API 只入队校验，RT<100ms | ✅ | /projects 与 /requests 扩展都是纯 DB 事务，无入队变更；SC-001 实测受理耗时 |
| 16 | WebSocket 推送运行状态 | ✅ | 不新增 WS 频道；项目筛选靠 URL + Query 失效刷新（与既有状态筛选同机制） |
| 17 | 部署 Compose + Caddy，密钥不落镜像 | ✅ | 仅迁移变更，部署流程不变 |
| 18 | JWT + RBAC 三角色 | ✅ | viewer 403/member、admin 可用；归属 404 纪律 |
| 19 | 凭证加密、不打印日志 | ✅ | 项目字段无凭证 |
| 20 | 用户脚本只跑沙箱 | ✅ | 无脚本执行 |

**违例登记（Complexity Tracking）**：无。本特性没有比「更简单方案」更复杂的取舍——
所有「被否决的更简单方案」（RESTRICT 删除、实时 JOIN、经手人可改归属）都被
spec/research 显式记录并说明了为什么不采用。

## Phase 拆分（每 Phase 一次提交，纯增量先行）

| Phase | 内容 | 任务 | 提交点 |
|---|---|---|---|
| P2 数据模型 | 模型 + 迁移 0012 + 状态/索引断言测试 | T001–T006 | `feat(db): 008 projects 表` |
| P3a 纯增量 | project_service + /projects 端点 + 审计常量 | T007–T012 | `feat(project): 008 项目 CRUD` |
| P3b 改既有 | requests +project_id/name（schema/service/端点/权限分流） | T013–T018 | `feat(request): 008 需求归属` |
| P4 前端 | api 层 + 管理页 + 表单选择器 + 看板筛选 + 卡片徽章 + 深链 | T019–T025 | `feat(ui): 008 项目容器前端` |
| P5 验收 | s9 脚本 + 回归 + 文档收尾 + 合并 | T026–T030 | 每项一次提交 |

并行组：P3a 内 service 测试与端点测试可并行（T007/T009 同文件族）；
P4 内管理页与看板筛选无相互依赖（T020/T022 可并行）。

## 风险与回滚

- **迁移 0012 加列加表**：向后兼容（可空列 + 新表），部署顺序仍是「先迁移后换服务」
  （deploy.sh 既有纪律），回滚 = `alembic downgrade 0011`（SET NULL 列随表删，
  数据丢失面仅项目实体本身——上线前 quickstart 记明）。
- **requests 谓词分流**：只新增「含 project_id 键时要求 owner」一支，
  不改 007 既有分支；既有集成测试（test_requests/test_dispatch_*）全量回归兜底。
- **project_name 漂移**：唯一写入点是「创建/编辑需求」与「项目改名」两处
  （service 层），集成测试对「改名后旧需求的 name」与「删项目后 name 清空」各一条。
