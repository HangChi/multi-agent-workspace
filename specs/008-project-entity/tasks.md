# Tasks: 008-project-entity

> Phase 拆分见 `plan.md`。每 Phase 一次提交；测试任务（红）先于实现任务（绿）。
> 批量改勾选必须按任务号精确匹配（前缀替换会误伤）；未实测的项不勾。

## Phase 2 — 数据模型（提交点：`feat(db): 008 projects 表`）

- [X] T001 [P] Write `backend/app/models/project.py`：`Project` 模型（id/owner_id/name≤100/description/status active|archived/timestamps）+ `UNIQUE(owner_id, name)` + `ix_projects_owner_status`
- [X] T002 Write `backend/alembic/versions/0012_project_entity.py`：建 `projects` 表 + `requests` +`project_id`（FK SET NULL）+`project_name`
- [X] T003 [P] Integration: `backend/tests/integration/test_project_schema.py` — 迁移后断言（projects 列/唯一约束/索引；requests 两列 + FK 的 ON DELETE SET NULL 行为：删项目→需求列空）
- [X] T004 Integration: downgrade 冒烟（0012→0011：两列与表消失，requests 数据不受影响——注意 downgrade 前项目数据会丢，测试用临时库）
- [X] T005 Modify `backend/app/models/request.py`：`project_id`/`project_name` 两列 + relationship（如有）
- [X] T006 空库复跑 `alembic upgrade head` → `0012`，psql 核对列/约束/索引（quickstart §4 回填）

## Phase 3a — 项目 CRUD 纯增量（提交点：`feat(project): 008 项目 CRUD`）

- [X] T007 [P] Unit/Integration: `backend/tests/integration/test_projects.py` — 先写红（P-1/P-2/P-3/P-4/P-8/P-9：建/同名 409/他人 404/viewer 403/计数/边界）
- [X] T008 Modify `backend/app/core/audit.py`：`project_create/project_update/project_delete` 三动作常量（集中定义区，同 REQUEST_DISPATCH_ACTIONS 风格）
- [X] T009 Write `backend/app/services/project_service.py`：list（带 request_count 实时聚合）/get/create/update（name 同步 requests.project_name，同事务）/delete（SET NULL + 清 project_name，同事务）/归档不改需求
- [X] T010 Write `backend/app/schemas/project.py`：`ProjectCreate/ProjectUpdate/ProjectOut/ProjectList`（request_count 字段）
- [X] T011 Write `backend/app/api/v1/projects.py`：五端点 + `main.py` 挂路由 + 审计（create/update/delete，delete detail 带 request_count）
- [X] T012 跑 T007 转绿 + 全量回归（`test_requests.py`/`test_dispatch_*.py` 不改一条）

## Phase 3b — 需求归属（改既有，提交点：`feat(request): 008 需求归属`）

- [X] T013 [P] Integration: `backend/tests/integration/test_request_project_link.py` — 先写红（建需求带项目 201/他人 404/归档 400；经手人改 project_id 403 但改标题 202；`?project_id=`/`?project=none` 过滤；删除项目后置空）
- [X] T014 Modify `backend/app/schemas/request.py`：`RequestCreate/RequestUpdate` +可选 `project_id`；`RequestOut` +`project_id`/`project_name`（可空默认 null——SC-006 零回归据此判）
- [X] T015 Modify `backend/app/services/request_service.py`：创建/编辑时的项目校验（存在+属本人+active）；编辑按「是否含 project_id 键」分流权限（R-05）；`list_requests` +`project_id`/`project=none` 过滤（与 scope 正交）
- [X] T016 Modify `backend/app/api/v1/requests.py`：list 加 query 参数；create/update 走 service 新校验；detail 无需改（schema 自带）
- [X] T017 跑 T013 转绿 + 既有测试全量回归（重点：test_requests/test_dispatch_visibility 的 403/404/202 断言不变）
- [ ] T018 [P] 前端 api 层先行：`frontend/src/api/projects.ts`（CRUD）+ `requests.ts` 类型扩展（T029 的测试会消费）——**挪到 Phase 4 与页面同批做**（独立文件无依赖，放 3b 提交里反而拆散前端改动）

## Phase 4 — 前端（提交点：`feat(ui): 008 项目容器前端`）

- [ ] T019 [P] Component: `frontend/src/pages/ProjectsPage.tsx` — 列表（名称/描述/状态/需求数）+ 新建 + 行内改名 + 归档切换 + 删除（ConfirmDialog，文案说明需求保留）
- [ ] T020 [P] Modify `frontend/src/components/request/RequestForm.tsx`：项目选择器（默认不归类，仅本人 active 项目）
- [ ] T021 Modify `frontend/src/pages/BoardPage.tsx` + `components/board/Board.tsx`：工具条项目下拉（全部/各项目带计数/未归属）+ URL `?project=` 深链 + 刷新保持
- [ ] T022 Modify `frontend/src/components/board/RequestCard.tsx`：项目名徽章（无归属不渲染）
- [ ] T023 Modify `frontend/src/components/layout/nav.ts`：`/projects` 导航项（member/admin 可见）
- [ ] T024 [P] Unit: `frontend/src/tests/projects.test.tsx` — 契约级用例（选择器默认不归类/归档项目不可选/下拉含未归属选项/viewer 无入口）
- [ ] T025 前端五件套（lint/format/typecheck/test/build）全绿

## Phase 5 — 验收与合并（每项一次提交）

- [ ] T026 Write `deploy/verify/s9_project_container.py` — 五场景（A 建项归类 / B 管理动作 / C 筛选深链 / D 权限边界 / E s8+s1–s4 回归）
- [ ] T027 部署 + 跑 s9 五场景全过 + 回填 quickstart §1/§4
- [ ] T028 回归：s8 原样重跑 7/7（在「需求有归属」的数据上）+ s1–s4 29/29 + strategy 3/3
- [ ] T029 前端浏览器走查逐条标 ⏳/✅（quickstart §2 清单，未验的写原因不勾）
- [ ] T030 README（核心能力/页面表/API 概览/已知限制）+ `docs/architecture.md`（§8 表 + 索引要点）+ docs/README 特性表 + ff 合并 master 删分支

## Notes（贯穿全清单的硬约束）

1. **零回归是硬约束**：`/requests` 既有响应只增可空字段；007 的所有集成测试
   （test_dispatch_visibility/actions/endpoints/notifications、test_requests）
   一条断言都不许改。改了就说明契约破了，回滚重做。
2. **快照双写点**：`project_name` 只由两处写——「建/改需求」（service）与
   「项目改名」（service 同事务）。任何第三处写入都是 bug；删项目时 service
   显式清空（外键 SET NULL 不清它）。
3. **权限分流只加一支**：`PATCH /requests` 仅在**请求体含 project_id 键**时
   要求创建者；其他路径维持 007 owner-or-assignee。用「键是否存在」而不是
   「值是否变化」判定——显式传同值也要 403，否则客户端无法预判行为。
4. **同名冲突含已归档项目**：归档项目仍占名字（contracts §1.2），防「归档后
   建同名」把历史归属搅浑。
5. **归档只拦新建**：已归档项目的既有归属、分派、接收、执行、退回全部照常
   （R-03）——任何「归档顺带冻结业务」的实现都是范围外发明。
6. 测试跑法同 007：`DATABASE_URL=postgresql+asyncpg://aw:test-password-123@127.0.0.1:5432/agent_workbench_test`；
   跑 pytest 时 export 的 DATABASE_URL 会污染 compose 变量替换（部署前 `env -u`）。
7. 迁移 0012 向后兼容（加表 + 可空列），deploy.sh 的「先迁移后换服务」纪律不变；
   downgrade 会丢项目实体本身，quickstart 必须写明这个回滚面。
