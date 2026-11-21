# Implementation Plan: 项目所有权移交（017）

## Technical Context

- 后端：FastAPI + SQLAlchemy 2.0 async + asyncpg，`AppError` 统一错误，
  `write_audit` 严格模式（审计失败即回滚）
- 前端：React 18 + TanStack Query + 既有 `Modal` / `Button` / `Select` / `Checkbox` 组件
- 测试：pytest（后端单测 + 集成）、vitest（前端）、e2e（B4 套件的 Playwright）
- 依赖：**零新增**
- 迁移：**无**

## Constitution Check

- [x] **I. SDD + TDD**：每个实现任务前有对应测试任务（tasks 里 T1xx 全部先于实现）
- [x] **I. 未实测不勾**：浏览器验收项在 Phase 5 实测后才勾
- [x] **II. 范围边界**：多 owner / 审批流 / 移交历史表 / 部门树 四项显式排除（spec Out of Scope）
- [x] **III. 完整系统**：后端端点 + 通知 + 审计 + 前端入口 + e2e，无半截环节
- [x] **IV. 质量门禁**：每 Phase ruff / mypy / pytest；前端 lint + typecheck + vitest
- [x] **016 错误码矩阵**：非相关人 404、参与者缺权 403、状态冲突 409（contracts 已逐条列）

## 实现策略

**核心原则：把权限迁移收进一个 service 函数，端点只做参数与审计编排。**
`transfer_ownership()` 里完成「加锁 → 五步校验 → 三个写动作 → 通知」，
端点负责 `write_audit` 与 commit —— 与 010 的 `add_member` 同构。

### Phase 1 — 契约与规格（本次提交）

spec / research / data-model / contracts / checklists / plan / tasks / quickstart。
无代码。

### Phase 2 — 后端 service + 端点（T1xx）

1. `app/services/project_member_service.py` 新增 `transfer_ownership()`：
   - `_load_project_for_member_ops` 复用（404/403 分工现成）；
   - 改用带 `FOR UPDATE` 的加载（新私有函数 `_load_project_for_update`）；
   - 五步校验（自移交 → 目标存在 → 目标角色 → 成员关系 → 同名）；
   - 写：`owner_id`、删成员行、按需插原 owner 行；
   - 通知新 owner。
2. `app/api/v1/projects.py` 加 `POST /{project_id}/transfer`，`ProjectTransferIn`
   放 `schemas/project.py`，审计常量 `PROJECT_TRANSFER`。
3. `remove_member` 的 409 文案更新 + 既有测试同步。

### Phase 3 — 前端（T2xx）

1. `ProjectsPage` 成员区上方加「移交」按钮（owner 且成员数 > 1 才渲染）。
2. `ProjectTransferDialog`：成员下拉 + 保留成员复选框 + 危险确认按钮。
3. `api/projects.ts` 加 `transferProject()`。
4. 成功后 `invalidateQueries(['projects'])` + 成员列表。

### Phase 4 — e2e（T3xx）

在 B4 套件加 `tests/test_transfer.py`：
- owner 经 UI 打开对话框、选择 mate、提交 → 列表徽章变化；
- mate 侧进入该项目并能改名（证明 owner 权限真的到位）；
- 同名冲突：API 造同名项目后走 UI 提交 → 错误消息原样显示。

### Phase 5 — 真实栈验收（T4xx）

quickstart 的八步 + 后端全量 + 前端全量 + e2e 全量。实测通过后才勾。

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| **误移交后无自助回退** | 危险按钮二次确认；审计可追溯；「反向移交」是同一端点的自然用法（新 owner 再移回来），不需要专门的回退 API |
| 同名冲突导致事务半提交 | 校验全部在写之前完成；`write_audit(strict=True)` 失败即回滚；集成测试显式断言「冲突后 owner 不变」 |
| 并发：移交 vs 改名/删除 | 项目行 `FOR UPDATE`；删除走同一行锁会等待 |
| 原 owner 保留成员后仍是成员→owner 动作 403 的困惑 | 前端不移交后仍显示「移交」按钮（`owner_id !== me` 立刻隐藏）；文案「移交后你仍是成员，可继续查看」 |
| 既有测试按旧 409 文案断言 | Phase 2 内同步修改，grep 全仓 `可删除项目` 确认无遗漏 |
