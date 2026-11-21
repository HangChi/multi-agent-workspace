# Tasks: 项目所有权移交（017）

> 勾选纪律（宪法 I）：只有**实测通过**的任务才改成 `[X]`；未跑过的保持 `[ ]`。
> `[P]` 表示可与同 Phase 内其它 `[P]` 并行。

## Phase 1 — 规格

- [X] T001 写 spec.md（问题陈述、5 个 US、FR-001~010、SC-001~004、Out of Scope）
- [X] T002 写 research.md（R-01~R-09：独立端点 / 目标须为成员 / 404 例外 / keep_as_member /
      校验顺序 / 显式同名预检 / 通知范围 / 迁移 / 锁策略）
- [X] T003 写 data-model.md（四个实体的变化、不变式、回滚面）
- [X] T004 写 contracts/api.md（端点、12 行错误矩阵、副作用、前端契约、文案变更 diff）
- [X] T005 写 plan.md（Constitution Check、五 Phase、风险表）
- [X] T006 写 checklists/requirements.md（Spec 质量 / 宪法 / 既有契约 / Notes）
- [X] T007 写 tasks.md（本文件）
- [X] T008 写 quickstart.md（八步真实栈验收）

## Phase 2 — 后端（测试先行）

- [X] T101 加 `schemas/project.py::ProjectTransferIn`（new_owner_id: UUID,
      keep_as_member: bool = True）+ `tests/unit/test_project_transfer_schema.py`（5 例）
- [X] T102 加 `core/audit.py::PROJECT_TRANSFER = "project_transfer"`
- [X] T103 写单测：五步校验的顺序与状态码
      （自移交 409 / 目标不存在 404 / viewer 409 / 非成员 409 / 同名 409）
- [X] T104 写集成测试：HTTP 层分支（401 / viewer 403 / 陌生人 404 / 成员 403 / 成功 200）
- [X] T105 写集成测试：成功路径的四个副作用
      （owner_id 变更、成员表增删、通知落库、审计落库）
- [X] T106 写集成测试：同名冲突后**归属不变**（事务回滚断言）
- [X] T107 写集成测试：keep_as_member=false 后原 owner 访问 404、
      scope=mine/relevant 都看不到
- [X] T108 实现 `project_member_service._load_project_for_transfer()`
      （`with_for_update()` 版本，404/403 分工与 `_load_project_for_member_ops` 一致）
- [X] T109 实现 `project_member_service.transfer_ownership()`：加锁 → 六步校验 →
      owner_id / 成员行 / 通知
- [X] T110 实现 `api/v1/projects.py::transfer_project()`（200 + ProjectOut + 审计）
- [X] T111 更新 `remove_member` 的 409 文案；grep 全仓 `可删除项目` 同步既有测试（0 处残留）
- [X] T112 把 `_assert_name_available` 公开为 `assert_name_available`（加 `message`
      参数供移交定制文案），同步两处既有调用点——不各写一份同名检查
- [X] T113 加迁移 `0017_project_ownership_transfer.py`（`notification_type` 加
      `project_ownership_received`）+ 模型 `Enum(...)` 声明同步
      （**规格初稿误判「无迁移」**：`notifications.type` 是 PG 枚举，不是字符串列）
- [X] T114 补 016 的漏项：`_load_owned_project` 让「成员做项目写操作」回 403 而非 404
      （016 契约 §3.6 要求 403，但 016 测试只覆盖了成员管理那一支）
- [X] T115 跑后端全量：ruff / ruff format / mypy / pytest（含覆盖率门禁）

## Phase 3 — 前端

- [X] T201 写 `components/project/__tests__/ProjectTransferDialog.test.tsx`（5 例：
      排除自己 / 默认勾选 / 未选人禁用 / keep=false 传参 / 错误消息原样）
- [X] T202 `api/projects.ts` 加 `transferProject(projectId, payload)`
- [X] T203 新建 `components/project/ProjectTransferDialog.tsx`
      （Modal + Select + 复选框 + 危险按钮）
- [X] T204 `ProjectsPage` 在操作组加「移交」入口（owner && member_count > 1）
- [X] T205 成功后 invalidate projects 与 projectMembers；失败透出后端 message
- [X] T206 跑前端：lint / typecheck / vitest 全量

## Phase 4 — e2e（B4 套件）

- [X] T301 加 `e2e/tests/test_transfer.py`：owner 经 UI 移交，徽章「我的」→「共享的」
- [X] T302 加同名冲突用例：API 造同名项目 → UI 提交 → 错误消息可见且弹层不关
- [X] T303 加接管人权限用例：mate 移交后能改名该项目（真权限，不只看徽章）
- [X] T304 跑 `e2e` 全量（原 14 例 + 新 3 例）

## Phase 5 — 真实栈验收与收尾

- [X] T401 新增 `deploy/verify/s13_project_transfer.py`（10 场景）并跑通；
      另按 quickstart 手工核对 23 条断言
- [X] T402 后端全量 + 前端全量 + e2e 全量三绿，记录数字
- [X] T403 文档同步：README 已知限制（010 的「移交是后续特性」→ 已实现）、
      `docs/README.md` 特性表加 017、特性数量 16→17
- [X] T404 本文件勾选回填 + 实测记录
- [ ] T405 合并回 master 并删除特性分支

## 实测记录（2026-11-21）

| 门禁 | 结果 |
|---|---|
| 后端 `pytest` + 覆盖率 | **2024 passed**（基线 2004，新增 20 例），覆盖率 **95.01%**（门槛 85%） |
| 后端 `ruff check` / `ruff format --check` / `mypy app` | 全部通过（149 源文件） |
| 前端 `vitest run` | **148 passed / 27 files**（基线 143，新增 5 例） |
| 前端 `eslint` / `tsc --noEmit` | 无输出（通过） |
| e2e（`e2e/`） | **17 passed**（原 14 + 新 3），21.5s |
| 真实栈 `s13_project_transfer.py` | **10/10 场景通过** |
| 迁移 | `0016 → 0017` 在真实库执行成功，枚举值已生效 |

**过程中的两个真发现**：

1. `notifications.type` 是 PostgreSQL 枚举而非 varchar——规格初稿断言「无迁移」，
   被集成测试当场打回（`invalid input value for enum notification_type`）。
   已补迁移并把教训写进 data-model §1。
2. 016 的契约 §3.6 要求「成员改项目元数据 → 403」，但 `_load_owned_project`
   把 owner 判定和存在性合并，成员实际拿到 404。016 的测试只覆盖了
   「成员管成员」那一支，所以一直没暴露。移交让「原 owner 变成成员」成为常见态，
   才把这个缺口顶出来——已在 T114 补齐（陌生人仍是 404，008 的 P-3 不变）。
