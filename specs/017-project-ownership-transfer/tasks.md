# Tasks: 项目所有权移交（017）

> 勾选纪律（宪法 I）：只有**实测通过**的任务才改成 `[X]`；未跑过的保持 `[ ]`。
> `[P]` 表示可与同 Phase 内其它 `[P]` 并行。

## Phase 1 — 规格（本次提交）

- [X] T001 写 spec.md（问题陈述、5 个 US、FR-001~010、SC-001~004、Out of Scope）
- [X] T002 写 research.md（R-01~R-09：独立端点 / 目标须为成员 / 404 例外 / keep_as_member /
      校验顺序 / 显式同名预检 / 通知范围 / 无迁移 / 锁策略）
- [X] T003 写 data-model.md（无 schema 变更、四个实体的变化、不变式、回滚面）
- [X] T004 写 contracts/api.md（端点、11 行错误矩阵、副作用、前端契约、文案变更 diff）
- [X] T005 写 plan.md（Constitution Check、五 Phase、风险表）
- [X] T006 写 checklists/requirements.md（Spec 质量 / 宪法 / 既有契约 / Notes）
- [X] T007 写 tasks.md（本文件）
- [X] T008 写 quickstart.md（八步真实栈验收）

## Phase 2 — 后端（测试先行）

- [ ] T101 加 `schemas/project.py::ProjectTransferIn`（new_owner_id: UUID,
      keep_as_member: bool = True）
- [ ] T102 加 `core/audit.py::PROJECT_TRANSFER = "project_transfer"`
- [ ] T103 写单测：五步校验的顺序与状态码
      （自移交 409 / 目标不存在 404 / viewer 409 / 非成员 409 / 同名 409）
- [ ] T104 写集成测试：HTTP 层分支（401 / viewer 403 / 陌生人 404 / 成员 403 / 成功 200）
- [ ] T105 写集成测试：成功路径的四个副作用
      （owner_id 变更、成员表增删、通知落库、审计落库）
- [ ] T106 写集成测试：同名冲突后**归属不变**（事务回滚断言）
- [ ] T107 写集成测试：keep_as_member=false 后原 owner 访问 404、
      scope=mine/relevant 都看不到
- [ ] T108 实现 `project_member_service._load_project_for_update()`
      （`with_for_update()` 版本，404/403 分工与 `_load_project_for_member_ops` 一致）
- [ ] T109 实现 `project_member_service.transfer_ownership()`：加锁 → 五步校验 →
      owner_id / 成员行 / 通知
- [ ] T110 实现 `api/v1/projects.py::transfer_project()`（200 + ProjectOut + 审计）
- [ ] T111 更新 `remove_member` 的 409 文案；grep 全仓 `可删除项目` 同步既有测试
- [ ] T112 跑后端全量：ruff / ruff format / mypy / pytest（含覆盖率门禁）

## Phase 3 — 前端

- [ ] T201 写 `components/project/__tests__/ProjectTransferDialog.test.tsx`：
      成员下拉排除自己、默认勾选保留、提交调 transfer、错误消息原样渲染
- [ ] T202 `api/projects.ts` 加 `transferProject(projectId, payload)`
- [ ] T203 新建 `components/project/ProjectTransferDialog.tsx`
      （Modal + Select + Checkbox + 危险按钮；焦点陷阱由 Modal 提供）
- [ ] T204 `ProjectsPage` 在成员区上方加「移交」入口（owner && member_count > 1）
- [ ] T205 成功后 invalidate projects 与 members 查询；失败透出后端 message
- [ ] T206 跑前端：lint / typecheck / vitest 全量

## Phase 4 — e2e（B4 套件）

- [ ] T301 加 `e2e/tests/test_transfer.py`：owner 经 UI 完成移交，列表徽章变「共享的」
- [ ] T302 加同名冲突用例：API 造同名项目 → UI 提交 → 错误消息可见
- [ ] T303 加接管人权限用例：mate 移交后能改名该项目（真权限，不只看徽章）
- [ ] T304 跑 `e2e` 全量（原 14 例 + 新 3 例）

## Phase 5 — 真实栈验收与收尾

- [ ] T401 按 quickstart 走完八步（curl + UI + 数据核对）
- [ ] T402 后端全量 + 前端全量 + e2e 全量三绿，记录数字
- [ ] T403 文档同步：README「已知限制」里 010 的「所有权移交是后续特性」改为已实现；
      `docs/README.md` 特性表加 017
- [ ] T404 本文件勾选回填（只勾实测通过的）+ 实测记录
- [ ] T405 合并回 master 并删除特性分支

## 实测记录

（Phase 2–5 完成后回填，不得预填）
