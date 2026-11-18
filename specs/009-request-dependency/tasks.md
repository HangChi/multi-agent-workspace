# Tasks: 009-request-dependency

> Phase 拆分见 `plan.md`。每 Phase 一次提交；测试任务（红）先于实现任务（绿）。
> 批量改勾选必须按任务号精确匹配（前缀替换会误伤）；未实测的项不勾。

## Phase 2 — 数据模型（提交点：`feat(db): 009 request_dependencies 表`）

- [X] T001 [P] Write `backend/app/models/request_dependency.py`：`RequestDependency` 模型（request_id/dependency_id/created_by/created_at）+ `UNIQUE(request_id, dependency_id)` + `CHECK (request_id <> dependency_id)` + 两个方向索引
- [X] T002 Modify `backend/app/models/request.py`：`dependencies` / `dependents` 两个 relationship（`cascade="all, delete-orphan"`, `passive_deletes=True`，同 dispatches 纪律）；**不加列**
- [X] T003 Write `backend/alembic/versions/0013_request_dependency.py`：建表 + 约束 + 索引
- [X] T004 [P] Integration: `backend/tests/integration/test_dependency_schema.py` — 迁移后断言（列/UNIQUE/CHECK/两索引；删需求→其边 CASCADE；删用户→created_by CASCADE）
- [X] T005 Integration: 空库复跑 `alembic upgrade head` → `0013` + downgrade 冒烟（quickstart §4 回填）
- [X] T006 [P] Unit: `backend/tests/unit/test_dependency_bfs.py` — 成环检测纯函数（无环/自环/二跳环/菱形非环/深链），BFS 可脱离 DB 测（R-04）

## Phase 3a — 依赖边纯增量（提交点：`feat(dependency): 009 依赖边 CRUD`）

- [X] T007 [P] Integration: `backend/tests/integration/test_dependencies.py` — 先写红（D-1~D-12 全用例：加边/自依赖 400/目标 404/重复 409/两环 409/删边/删不存在 404/他人 404/viewer 403/经手人可删/反向视图）
- [X] T008 Modify `backend/app/core/audit.py`：`request_dependency_add` / `request_dependency_remove` 两动作
- [X] T009 Write `backend/app/services/dependency_service.py`：add（自依赖/重复/成环判定）/ remove / list_dependencies（带标题状态）/ list_dependents / **assert_not_blocked（门禁半连接查询，返回未完成前置列表）**
- [X] T010 Write `backend/app/schemas/dependency.py`：`DependencyEdgeCreate/Out/List`
- [X] T011 Write 边端点：`backend/app/api/v1/dependencies.py`（挂在 `/requests/{id}/dependencies` 与 `/dependents`）+ main.py 挂路由 + 审计
- [X] T012 跑 T007 转绿 + 全量回归

## Phase 3b — 门禁与通知（改既有，提交点：`feat(assign): 009 硬门禁与解锁通知`）

- [X] T013 [P] Integration: `backend/tests/integration/test_dependency_gate.py` — 先写红（未完成前置 assign 409 逐条；completed/failed/cancelled 解锁；user 落点不查；调度撞门禁建单不分配）
- [X] T014 Modify `backend/app/services/assignment_service.py`：assign 在目标校验前插 `assert_not_blocked`（一次半连接）；409 消息逐条（>5 截断）；user 落点与重试路径不查
- [X] T015 Modify `backend/app/services/request_service.py`：`get_request` 详情派生 `dependencies` + `blocked`；`list_requests` 两查询算法补 `blocked`（data-model §4）
- [X] T016 Modify `backend/app/services/request_service.py`：`settle_request_status` 终态后反查后续方、逐条判「其余前置均终态」→ 发 `dependency_ready`（owner+assignee 去重；后续已终态/running 跳过）
- [X] T017 Modify `backend/app/services/schedule_service.py`：`_fire_and_assign` 调 assign 前查门禁；未满足则跳过分配（trigger=fired + reason 注明，需求留 created）——**不走** AppError→failed 路径（R-07）
- [X] T018 跑 T013 转绿 + 既有测试全量回归（重点：test_assign/test_dispatch_*/test_schedules 一条断言不改）

## Phase 4 — 前端（提交点：`feat(ui): 009 依赖前端`）

- [ ] T019 [P] `frontend/src/api/dependencies.ts`：add/remove/list/dependents 四个函数 + endpoints 挂载
- [ ] T020 [P] Component: `frontend/src/components/request/DependencySection.tsx` — 详情弹层「前置需求」区块（边列表 + 添加器 + 移除 + 被挡提示）
- [ ] T021 Modify `frontend/src/pages/BoardPage.tsx`：详情弹层挂 DependencySection；`blocked` 透传给卡片
- [ ] T022 Modify `frontend/src/components/board/RequestCard.tsx`：未完成前置徽章（数量 + title 逐条），`blocked=false` 不渲染
- [ ] T023 Modify `frontend/src/components/layout/NotificationBell.tsx`：TYPE_META 加 `dependency_ready`（图标 clock，标签「前置就绪」）
- [ ] T024 [P] Unit: `frontend/src/tests/dependencies.test.tsx` — 契约级用例（徽章仅 blocked 渲染/通知类型元数据/详情区块空态）
- [ ] T025 前端五件套（lint/format/typecheck/test/build）全绿

## Phase 5 — 验收与合并（每项一次提交）

- [ ] T026 Write `deploy/verify/s10_request_dependency.py` — 四场景（A 连边成环 / B 硬门禁 / C 解锁通知 / D 调度+s9/s8/s1–s4 回归）
- [ ] T027 部署 + 跑 s10 四场景全过 + assign RT 计时（SC-003）+ 回填 quickstart §1/§4
- [ ] T028 回归：s9 5/5、s8 7/7、s1–s4 29/29、strategy 3/3 原样重跑
- [ ] T029 前端浏览器走查逐条标 ⏳/✅（quickstart §2 清单，未验的写原因不勾）
- [ ] T030 README（核心能力/页面表/API 概览/已知限制）+ `docs/architecture.md`（§8 表 + 索引要点 + 语义要点）+ docs/README 特性表 + ff 合并 master 删分支

## Notes（贯穿全清单的硬约束）

1. **assign 只插一步、只查一次**：门禁是 `assert_not_blocked` 的**一次半连接**，
   插在目标校验前、配额后。不得在 assign 里逐条查前置（N+1 会吃掉
   SC-003 的 100ms 预算）；不得在 user 落点/重试路径查（FR-005/R-08）。
2. **门禁消息必须逐条**：`"有 2 个前置需求未完成：A（执行中）、B（待处理）"`——
   只说数量的 409 等于让人去猜。超过 5 条截断为「…等 N 个」（消息不是清单 UI）。
3. **settle 是通知唯一落点**：不得在 execute_run 各完成分支发（R-02）——
   settle 的 running 门禁免费保证幂等；重复发通知比不发更伤信任。
4. **调度撞门禁必须建单不分配**（FR-009/R-07）：走 AppError→failed 旧路径会
   rollback 连需求一起丢，且连续失败会 auto-disable 规则。新支只加不换。
5. **详情端派生、列表端只给布尔**：`dependencies` 明细只在
   `GET /requests/{id}`；列表端 `blocked` 用两查询算法（data-model §4），
   不得逐条查边（N+1）。
6. **模型命名中性**：`request_id`=被依赖方、`dependency_id`=前置，
   不叫 blocker/blocked（表存结构，不存叙事）。
7. 测试跑法同前：`DATABASE_URL=postgresql+asyncpg://aw:test-password-123@127.0.0.1:5432/agent_workbench_test`；
   跑 pytest 时 export 的 DATABASE_URL 会污染 compose 变量替换（部署前 `env -u`）。
8. 迁移 0013 纯加表向后兼容；downgrade 丢边数据（特性实体本身），
   quickstart 必须写明回滚面。
