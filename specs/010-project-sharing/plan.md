# Implementation Plan: 010 项目共享与成员

**Date**: 2026-11-18 | **Branch**: `feat/010-project-sharing` | **Spec**: `specs/010-project-sharing/spec.md`

## Technical Context

- 后端：FastAPI + SQLAlchemy 2.0 async + Alembic（迁移 **0014**）+ Celery + Redis；PostgreSQL 16。
- 前端：React 18 + TS + Vite + Tailwind + dnd-kit + TanStack Query + Zustand。
- 新增依赖：**零**（成员管理复用既有路由/service/Modal/Select 全部模式）。
- 涉及既有文件（全部为受控增量，扩集合不改码）：
  - `models/project_member.py`（新）、`models/project.py`（relationship）
  - `services/project_service.py`（成员 CRUD + 四处口径扩展 + 计数口径）
  - `services/request_service.py`（`_check_owner` 加一肢 + 列表口径 + project_id 分流）
  - `api/v1/projects.py`（成员端点）、`api/v1/requests.py`（谓词走 service，端点层少改）
  - `core/audit.py`（+2 动作）、`models/notification.py`（+2 枚举值）
  - 前端：`api/projects.ts`、`ProjectsPage.tsx`（成员区）、铃铛 TYPE_META
- 验收：pytest（真实 PG）+ vitest + 真实栈验收脚本 `deploy/verify/s11_project_sharing.py`。

## Constitution Check

| # | 宪法原则（v1.1.0） | 符合性 | 结论 |
|---|---|---|---|
| 1 | I. SDD+TDD：先 spec 再实现 | ✅ | 九件套先于代码 |
| 2 | I. Red-Green-Refactor 强制 | ✅ | P2/P3/P4 均测试任务先 |
| 3 | I. 测试未过不标记完成 | ✅ | 每 Phase 跑门禁后才回勾 |
| 4 | II. spec 先行，四步走 | ✅ | 本目录九件套 |
| 5 | II. FR 必有 Given/When/Then 锚点 | ✅ | FR-001~012 挂 US 场景；M-1~M-12 可执行 |
| 6 | II. 范围显式写「不含」 | ✅ | FR-012 + data-model §7 六条不做 |
| 7 | III. 完整系统非 MVP | ✅ | 成员 CRUD/口径/通知/审计/前端管理齐备 |
| 8 | IV. 每 Phase 质量门禁 | ✅ | 每 Phase ruff+mypy+pytest；P4 前端五件套；P5 s11 + 四脚本回归 |
| 9 | 技术栈零新增 | ✅ | 纯用既有栈 |
| 10 | 前端栈不破 | ✅ | 全用既有组件 |
| 11 | PG16 + Redis + S3 不变 | ✅ | 仅加表 |
| 12 | LLM 经 LiteLLM | ✅ | 无 LLM 调用 |
| 13 | 自研编排器禁 LangGraph | ✅ | 不碰执行引擎 |
| 14 | 沙箱隔离 | ✅ | 无代码执行 |
| 15 | API RT < 100ms | ✅ | 成员展开是一次半连接；SC-006 实测 |
| 16 | WebSocket 推送 | ✅ | 不新增 WS 频道；成员变动走通知表（轮询链路） |
| 17 | 部署 Compose + Caddy | ✅ | 仅迁移；deploy.sh 纪律不变 |
| 18 | JWT + RBAC 三角色 | ✅ | viewer 403；owner/成员/陌生人三级谓词 |
| 19 | 凭证加密 | ✅ | 无凭证字段 |
| 20 | 脚本只跑沙箱 | ✅ | 无脚本执行 |

**违例登记（Complexity Tracking）**：无。唯一「看起来更简单」的方案
（成员表加 role 列）被 R-01 用「现在只有一个非 owner 值」否掉。

## Phase 拆分（每 Phase 一次提交，纯增量先行）

| Phase | 内容 | 任务 | 提交点 |
|---|---|---|---|
| P2 数据模型 | `project_members` 模型 + 迁移 0014 + schema 断言测试 | T001–T005 | `feat(db): 010 project_members 表` |
| P3a 纯增量 | 成员管理 service + 端点 + 审计 + 通知 + 枚举 | T006–T010 | `feat(project): 010 成员管理端点` |
| P3b 改既有 | 四处口径扩展（projects/requests）+ 谓词扩展 + project_id 分流 | T011–T016 | `feat(scope): 010 共享可见性与协作谓词` |
| P4 前端 | api 层 + 管理页成员区 + 我的/共享徽章 + 铃铛类型 | T017–T021 | `feat(ui): 010 共享前端` |
| P5 验收 | s11 脚本 + 四脚本回归 + 文档收尾 + 合并 | T022–T026 | 每项一次提交 |

并行组：P3a 内成员端点测试与通知断言可并行；P4 内成员区与徽章无相互依赖。

## 风险与回滚

- **谓词扩展的回归面**：`_check_owner` 是 007 锁定函数。策略是**不改它本身**，
  在调用方（update/assign/dependency）加「或项目成员」的第二判据——
  测试里那些「陌生人 403」的用例全部仍是非成员，一条不断（SC-004）。
  若回归翻车，回滚 = 撤掉调用方的第二判据，`_check_owner` 从未被动过。
- **列表口径的 OR EXISTS**：`GET /requests` 加一肢半连接。数据量下
  （22 项目/21 需求）无可测影响；SC-006 用真实栈计时兜底。
- **迁移 0014**：纯加表，向后兼容；downgrade 丢成员关系（特性实体），
  quickstart 记明回滚面。
- **枚举加值**：`notification_type` +2 值走 autocommit_block（同 007/009）。
