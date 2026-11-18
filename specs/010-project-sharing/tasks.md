# Tasks: 010-project-sharing

> Phase 拆分见 `plan.md`。每 Phase 一次提交；测试任务（红）先于实现任务（绿）。
> 批量改勾选必须按任务号精确匹配（前缀替换会误伤）；未实测的项不勾。

## Phase 2 — 数据模型（提交点：`feat(db): 010 project_members 表`）

- [X] T001 [P] Write `backend/app/models/project_member.py`：`ProjectMember` 模型（project_id/user_id/created_at，复合主键，两 FK CASCADE）+ `ix_project_members_user_id`；**无 role 列**（R-01）
- [X] T002 Modify `backend/app/models/project.py`：`members` relationship（cascade + passive_deletes）；`backend/app/models/__init__.py` 注册 ProjectMember（`__all__` 同步，防 009 的 F401 返工）
- [X] T003 Write `backend/alembic/versions/0014_project_member.py`：建表 + PK + 索引 + notification_type 枚举加两值（autocommit_block）
- [X] T004 Modify `backend/app/models/notification.py`：type 枚举加 `project_member_added` / `project_member_removed`
- [X] T005 [P] Integration: `backend/tests/integration/test_project_member_schema.py` — 迁移后断言（列/PK/索引；删用户→成员行 CASCADE 但需求保留；删项目→成员行 CASCADE）

## Phase 3a — 成员管理纯增量（提交点：`feat(project): 010 成员管理端点`）

- [ ] T006 [P] Integration: `backend/tests/integration/test_project_members.py` — 先写红（M-1~M-12 全用例：加人 201+通知/重复 409/停用 400/不存在 404/成员加人 403/他人 404/自移除 409/移除不存在 404/列表含 owner/成员可见列表/删项目级联且无通知）
- [ ] T007 Modify `backend/app/core/audit.py`：`project_member_add` / `project_member_remove` 两动作
- [ ] T008 Write `backend/app/services/project_member_service.py`：add（在职校验/PK 冲突 409/通知）/ remove（自移除 409/通知）/ list（含 owner 标记）/ `is_member` / `member_project_ids`（relevant 口径反查）
- [ ] T009 Write `backend/app/schemas/project_member.py`：`ProjectMemberCreate/Out/List`（Out 带 user_email + is_owner）
- [ ] T010 Write 成员端点：`backend/app/api/v1/project_members.py`（挂 `/projects/{id}/members`）+ main.py 挂路由 + 审计；跑 T006 转绿 + 全量回归

## Phase 3b — 口径与谓词扩展（改既有，提交点：`feat(scope): 010 共享可见性与协作谓词`）

- [ ] T011 [P] Integration: `backend/tests/integration/test_sharing_scope.py` — 先写红（成员 relevant 列表含项目与需求/详情 200/移除后 404；成员协作链建→改→分配→连边；改归属 403；accept 仍仅 assignee；陌生人维持 403/404）
- [ ] T012 Modify `backend/app/services/project_service.py`：`_load_owned_project` → 可见性版本（owner ∪ 成员，用于详情/成员列表读）；`load_active_owned_project` → active 且（owner ∪ 成员）（建需求校验）；`project_visible_to_user` 加「成员」一肢；`_visible_request_counts` 口径扩展
- [ ] T013 Modify `backend/app/services/project_service.py`：`list_projects` relevant 口径加「我是成员的」；`get_project` 返回 member_count
- [ ] T014 Modify `backend/app/services/request_service.py`：`_check_owner` **不动**；新增 `_can_edit_request(request, user_id, is_member)` 第二判据，update/assign/dependency 调用方加一肢；`list_requests` relevant 加项目成员半连接；`update_request` 的 project_id 分流改为「创建者或该项目 owner」（R-03）
- [ ] T015 Modify `backend/app/api/v1/projects.py`：详情/列表响应带 member_count；`GET /projects/{id}` 谓词走 service 新可见性
- [ ] T016 跑 T011 转绿 + **既有测试全量回归**（重点：test_dispatch_visibility/test_requests 的陌生人 403 一条不改）

## Phase 4 — 前端（提交点：`feat(ui): 010 共享前端`）

- [ ] T017 [P] `frontend/src/api/projectMembers.ts`：add/remove/list + endpoints 挂载；projects.ts 类型加 member_count/is_shared
- [ ] T018 [P] Component: `frontend/src/components/project/MemberSection.tsx` — 成员区（owner 徽章 + 成员列表 + 添加器 + 移除；owner 才见操作）
- [ ] T019 Modify `frontend/src/pages/ProjectsPage.tsx`：挂 MemberSection；「我的/共享的」徽章
- [ ] T020 Modify `frontend/src/components/layout/NotificationBell.tsx`：TYPE_META 加 project_member_added/removed（图标 users）
- [ ] T021 [P] Unit: `frontend/src/tests/projectMembers.test.tsx` — 契约级用例（owner 徽章/成员只读/添加器候选取 users/options/viewer 无入口）；前端五件套

## Phase 5 — 验收与合并（每项一次提交）

- [ ] T022 Write `deploy/verify/s11_project_sharing.py` — 四场景（A 成员管理 / B 可见性 / C 协作链 / D 回归+RT）
- [ ] T023 部署 + 跑 s11 四场景全过 + RT 计时（SC-006）+ 回填 quickstart §1/§4
- [ ] T024 回归：s10 4/4、s9 5/5、s8 7/7、s1–s4 29/29、strategy 3/3 原样重跑
- [ ] T025 前端浏览器走查逐条标 ⏳/✅（quickstart §2 清单，未验的写原因不勾）
- [ ] T026 README（核心能力/页面表/API 概览/已知限制）+ `docs/architecture.md`（§8 表 + 索引 + 语义要点）+ docs/README 特性表 + ff 合并 master 删分支

## Notes（贯穿全清单的硬约束）

1. **`_check_owner` 本身一行不动**（plan 风险节）：谓词扩展靠调用方的
   第二判据（`_can_edit_request` 的「或项目成员」）。既有测试里那些
   「陌生人 403」全部仍是非成员——回归翻了就是第二判据写错，回滚 =
   撤判据，锁定函数从未被动。
2. **错误码纪律**：成员做 owner 动作 = 403（他知道项目存在，不够权）；
   陌生人碰项目一切 = 404（不暴露存在性）。两种「不够权」不要混码。
3. **project_id 变更 = 创建者或该项目 owner**（R-03）：请求体含该键时
   单独判定；其余字段维持 owner ∪ assignee ∪ 成员。用「键是否存在」
   判定（同 008），不用「值是否变化」。
4. **删项目不发成员通知**（R-05）：点进 404 的通知是噪声；级联删成员行
   但 notifications 一条不建。
5. **成员相关查询全部走半连接**（`EXISTS project_members`）：
   不得先查成员列表再 `IN (...)` 拼进主查询——两趟查询在并发下有
   中间态，且 SQL 冗长。一肢 EXISTS 原子且可索引。
6. **前端选择器/下拉不额外改**：scope=relevant 后端口径扩展自动带出
   共享项目——把口径收敛在后端是 008/009 的既有收益，别在前端复制一套。
7. 测试跑法同前：`DATABASE_URL=postgresql+asyncpg://aw:test-password-123@127.0.0.1:5432/agent_workbench_test`；
   跑 pytest 时 export 的 DATABASE_URL 会污染 compose 变量替换（部署前 `env -u`）。
8. 迁移 0014 纯加表向后兼容；downgrade 丢成员关系（特性实体本身），
   quickstart 必须写明回滚面。
