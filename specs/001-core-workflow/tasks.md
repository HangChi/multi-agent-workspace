# Tasks: 核心工作流（Core Workflow）

**Input**: Design documents from `/specs/001-core-workflow/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/api.md, quickstart.md

**Tests**: 本项目宪法强制 SDD+TDD（NON-NEGOTIABLE），每个用户故事的测试任务**必须先写、先红（FAIL）**，再实现使其变绿（PASS）。测试是必须项，不是可选。

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- Web app: `backend/`（FastAPI 后端）、`frontend/`（React SPA）、`deploy/`（部署编排）
- 提交规范（宪法）：实现阶段**每个 Phase 一次提交**，形如 `feat(<域>): <内容>（T0xx-T0yy）`；每个 Phase 结束后**必须勾选 tasks.md 对应任务并提交 tasks.md 状态更新**（`docs(tasks): 更新 NNN-<name> 任务状态`）。

---

## Phase 1: Setup（共享基础设施）

**Purpose**: 项目初始化与基础工程结构

- [x] T001 创建 monorepo 目录骨架：backend/app/{api,core,models,schemas,services,orchestrator,executors,tasks}、backend/tests/{unit,integration}、backend/scripts、frontend/src/{api,components,hooks,stores,pages}、deploy、docs（mkdir -p 逐层创建并放置 .gitkeep）
- [x] T002 后端工程初始化：backend/pyproject.toml（FastAPI、SQLAlchemy 2.0 async、Alembic、Celery 5、Redis、LiteLLM、Pydantic v2、structlog、pytest、pytest-asyncio、pytest-cov、httpx）+ backend/pytest.ini
- [x] T003 [P] 前端工程初始化：frontend/package.json（React 18、TypeScript 5、Vite、TailwindCSS、dnd-kit、TanStack Query、Zustand、axios）+ frontend/vite.config.ts + frontend/tsconfig.json + frontend/tailwind.config.js + frontend/index.html
- [x] T004 [P] 后端代码质量工具：pyproject.toml 配置 ruff（lint）+ mypy（类型检查），backend/.ruff.toml
- [x] T005 [P] 前端代码质量工具：frontend/.eslintrc.cjs + frontend/.prettierrc（eslint + prettier）
- [x] T006 [P] 基础设施编排：deploy/docker-compose.yml（postgres:16 / redis:7 / minio）+ deploy/.env.example（DATABASE_URL/REDIS_URL/MINIO_*/JWT_SECRET/LLM_API_KEY/INVITE_CODE，不含真实密钥）+ deploy/README.md
- [x] T007 [P] 仓库文档：docs/README.md（项目简介、目录结构、本地启动方式）

**Checkpoint**: 工程骨架可构建，lint 工具可运行

---

## Phase 2: Foundational（阻塞性基础，必须先于所有用户故事）

**Purpose**: 核心基础设施——数据层、安全基座、事件总线、任务队列、API 依赖注入

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T008 建全部 SQLAlchemy 模型：backend/app/models/（user.py、invite_code.py、agent.py、group.py、request.py、run.py、step.py、artifact.py、audit_log.py），对齐 data-model.md 字段/约束/索引/唯一约束（含 group_members 的 (group_id,agent_id)/(group_id,position) 唯一、steps 的 (run_id,seq) 唯一）
- [x] T009 Alembic 初始化与初始迁移：backend/alembic/env.py + backend/alembic/versions/0001_initial.py（全部 10 张表，含 enum 类型）
- [x] T010 环境配置：backend/app/core/config.py（pydantic-settings，字段对齐 deploy/.env.example，启动时校验必需项）
- [x] T011 数据库访问：backend/app/core/db.py（async engine + async_sessionmaker + get_db 依赖，连接池配置）
- [x] T012 [P] 安全基座：backend/app/core/security.py（bcrypt 密码哈希 + JWT access/refresh 签发与校验，token 载荷含 user_id/role/type/exp）
- [x] T013 [P] 审计日志：backend/app/core/audit.py（write_audit(user_id, action, target_type, target_id, detail)，structlog 写入 audit_logs 表）
- [x] T014 [P] 事件总线：backend/app/core/events.py（Redis Pub/Sub 封装：publish_run_event(run_id, payload) + 订阅者接口，频道命名 `run:{run_id}`）
- [x] T015 [P] Celery 基座：backend/app/tasks/celery_app.py（broker=Redis，任务序列化配置，结果后端）+ backend/app/worker.py（Worker 入口）
- [x] T016 API 依赖注入：backend/app/api/deps.py（get_current_user 解析 JWT、require_role(role) 角色权限依赖、get_owned_resource 资源归属校验，返回 401/403）
- [x] T017 统一错误处理：backend/app/api/errors.py（AppError 异常体系 + FastAPI exception handler，输出 `{"error":{"code","message"}}`，对齐 contracts/api.md 错误码表 400/401/403/404/409/422/429）
- [x] T018 测试基座：backend/tests/conftest.py（async 测试引擎、测试数据库 fixture（Alembic 迁移或 create_all）、Redis fixture、MinIO fixture/mock、httpx AsyncClient fixture、JWT 签发辅助、随机用户工厂）

**Checkpoint**: 数据库迁移可执行、安全/事件/队列基座就绪、测试可跑——用户故事可开始

---

## Phase 3: User Story 1 - 注册登录与权限控制（Priority: P1）🎯 MVP

**Goal**: 邀请码注册、登录/刷新、三角色 RBAC（admin/member/viewer）、资源归属校验、前端路由守卫

**Independent Test**: 注册新账号 → 登录 → 验证不同角色可见范围与操作权限（SC-001、SC-005）

### Tests for User Story 1（先写，先红后绿）⚠️

- [ ] T019 [P] [US1] 认证集成测试 backend/tests/integration/test_auth.py：有效邀请码注册 201 / 无效/过期/用尽码 400 / 邮箱重复 409 / 登录成功 200 / 密码错误与禁用账号 403 / refresh 换发 / me 返回当前用户
- [ ] T020 [P] [US1] 权限集成测试 backend/tests/integration/test_permissions.py：viewer 创建 Agent 403、member 访问 admin 接口 403、未认证 401、非 owner 访问他人资源 403

### Implementation for User Story 1

- [ ] T021 [US1] 认证 schema：backend/app/schemas/auth.py（RegisterRequest/LoginRequest/TokenResponse/UserOut，Pydantic v2 校验邮箱与密码强度）
- [ ] T022 [US1] 认证服务：backend/app/services/auth_service.py（注册：校验邀请码 used_count<max_uses 且未过期、事务内消耗邀请码、密码哈希；登录；refresh 轮换；me）
- [ ] T023 [US1] 认证端点：backend/app/api/v1/auth.py（POST /auth/register、POST /auth/login、POST /auth/refresh、GET /auth/me）
- [ ] T024 [US1] 引导脚本：backend/scripts/bootstrap.py（CLI：创建首个 admin 用户 + 生成管理员邀请码，输出到 stdout，供 quickstart S1 使用）
- [ ] T025 [US1] 前端认证态：frontend/src/stores/authStore.ts + frontend/src/hooks/useAuth.ts + frontend/src/api/endpoints.ts（auth 部分）+ frontend/src/pages/LoginPage.tsx + frontend/src/pages/RegisterPage.tsx
- [ ] T026 [US1] 前端路由与守卫：frontend/src/router.tsx（登录态守卫 + 角色守卫：admin 可进 AdminPage，member/viewer 403 提示）+ frontend/src/pages/AdminPage.tsx（权限占位页）
- [ ] T027 [US1] 审计挂接：login/register 写 audit_logs（backend/app/api/v1/auth.py 内调用 core/audit.py）
- [ ] T028 [US1] 集成验证：S1 场景全过（注册→登录→me→viewer 403→member 403），SC-001/SC-005 达标（按 specs/001-core-workflow/quickstart.md S1 执行，结果记入验证记录表）

**Checkpoint**: US1 独立可用——安全的用户身份与权限体系交付

---

## Phase 4: User Story 2 - Agent 与 Agent 组管理（Priority: P1）

**Goal**: 成员创建/编辑/归档/删除自己的 Agent（提示词/模型/温度/步数/工具/成本上限），创建组、增删成员并排序、配置路由策略

**Independent Test**: 创建 Agent → 创建组 → 组内增删排序 Agent → 配置路由策略（交付「可配置的多角色执行单元」）

### Tests for User Story 2（先写，先红后绿）⚠️

- [ ] T029 [P] [US2] Agent 集成测试 backend/tests/integration/test_agents.py：创建 201 / 编辑 200 / 归档 200 / 删除 204 / 被组引用删除 409（仅可归档）/ 非 owner 编辑 403 / 归档 Agent 不可出现在可分配列表
- [ ] T030 [P] [US2] 组集成测试 backend/tests/integration/test_groups.py：创建 201（含 routing_strategy 默认 fan_out）/ 添加成员 201 / 重复成员 409 / 位置冲突 409 / 移除成员 204 / 排序保存 / 删除组级联清空成员 204 / 非 owner 操作 403

### Implementation for User Story 2

- [ ] T031 [US2] Agent schema：backend/app/schemas/agent.py（AgentCreate/AgentUpdate/AgentOut，校验 tools 白名单、cost_limit_cents>0、max_steps 范围）
- [ ] T032 [US2] 组 schema：backend/app/schemas/group.py（GroupCreate/GroupUpdate/GroupWithMembers，routing_strategy 枚举校验）
- [ ] T033 [US2] Agent 服务：backend/app/services/agent_service.py（CRUD + archive + 删除前检查组引用 + 归属校验）
- [ ] T034 [US2] 组服务：backend/app/services/group_service.py（CRUD + 成员增删（position 唯一处理）+ 级联清理 + 归属校验）
- [ ] T035 [US2] Agent 端点：backend/app/api/v1/agents.py（GET/POST /agents、PATCH /agents/{id}、POST /agents/{id}/archive、DELETE /agents/{id}，含 `?status=&q=` 过滤）
- [ ] T036 [US2] 组端点：backend/app/api/v1/groups.py（GET/POST /groups、PATCH /groups/{id}、POST /groups/{id}/members、DELETE /groups/{id}/members/{agent_id}、DELETE /groups/{id}）
- [ ] T037 [US2] 前端 Agent 管理：frontend/src/pages/AgentPage.tsx + frontend/src/components/agent/（列表/创建表单/编辑/归档/删除，TanStack Query 缓存）
- [ ] T038 [US2] 前端组管理：frontend/src/pages/GroupPage.tsx + frontend/src/components/group/（列表/创建/成员管理/排序/策略选择）
- [ ] T039 [US2] 审计挂接：在 backend/app/api/v1/agents.py 与 backend/app/api/v1/groups.py 内调用 core/audit.py 的 write_audit（agent_create/agent_update/agent_archive/agent_delete/group_create/group_member_change）
- [ ] T040 [US2] 集成验证：S2 场景全过（含非 owner 403）（按 specs/001-core-workflow/quickstart.md S2 执行，结果记入验证记录表）

**Checkpoint**: US1 + US2 独立可用——执行单元与组合管理交付

---

## Phase 5: User Story 3 - 需求创建与看板（Priority: P1）

**Goal**: 需求卡片 CRUD（标题/描述/期望产出/优先级/附件）、看板按状态分组 + 关键词搜索、附件预签名上传下载

**Independent Test**: 创建需求 → 上传附件 → 看板按状态展示 → 搜索定位（交付「需求管理与可视化看板」）

### Tests for User Story 3（先写，先红后绿）⚠️

- [ ] T041 [P] [US3] 需求集成测试 backend/tests/integration/test_requests.py：创建 201（status=created）/ 详情 200 / 分页与 `?status=&q=&priority=` 过滤 / 归属校验 403 / 404
- [ ] T042 [P] [US3] 附件测试 backend/tests/integration/test_attachments.py：上传 201（预签名 URL 可 PUT）/ 超 20MB 拒绝 400 / 不支持类型拒绝 400 / 下载 302 预签名 / 附件元数据回填 requests.attachments

### Implementation for User Story 3

- [ ] T043 [US3] 需求 schema：backend/app/schemas/request.py（RequestCreate/RequestUpdate/RequestOut，priority/status 枚举）
- [ ] T044 [US3] 需求服务：backend/app/services/request_service.py（CRUD + 列表查询（状态/关键词/优先级/分页）+ 归属校验）
- [ ] T045 [US3] 制品服务：backend/app/services/artifact_service.py（MinIO 客户端 + 预签名 PUT/GET URL + 大小/类型校验（≤20MB，白名单 mime））
- [ ] T046 [US3] 需求端点：backend/app/api/v1/requests.py（GET/POST /requests、GET /requests/{id}、POST /requests/{id}/attachments）
- [ ] T047 [US3] 前端看板：frontend/src/pages/BoardPage.tsx + frontend/src/components/board/Board.tsx + frontend/src/components/board/RequestCard.tsx（按状态分组列：待分配/执行中/已完成/失败/已取消 + 关键词搜索框）
- [ ] T048 [US3] 前端需求交互：frontend/src/components/request/（创建需求表单 + 附件上传组件，调预签名 PUT）
- [ ] T049 [US3] 审计挂接：在 backend/app/api/v1/requests.py 内调用 core/audit.py 的 write_audit（request_create）
- [ ] T050 [US3] 集成验证：S3 场景全过（按 specs/001-core-workflow/quickstart.md S3 执行，结果记入验证记录表）

**Checkpoint**: US1-3 独立可用——需求管理与看板交付

---

## Phase 6: User Story 4 - 拖拽分配与任务入队（Priority: P1）

**Goal**: 需求卡拖到 Agent（direct）或组（选策略，默认取组配置），校验后建 Run 入队，需求状态变执行中，接口 RT<100ms

**Independent Test**: 拖需求到 Agent → 确认 → 202 受理 → 卡片变执行中 → 队列出现任务（SC-002）

### Tests for User Story 4（先写，先红后绿）⚠️

- [ ] T051 [P] [US4] 分配集成测试 backend/tests/integration/test_assign.py：拖单 Agent 202（strategy=direct）/ 拖组 fan_out 202 生成 N 个 Run（含 parent_run_id）/ 需求已在执行 409 / 目标已归档 400 / 目标不存在或无权限 404 / 入队后需求 status=running
- [ ] T052 [P] [US4] 策略单元测试 backend/tests/unit/test_strategies.py：direct 展开 1 个执行目标 / fan_out 展开组内全部 active Agent（按 position 排序）/ pipeline、router 策略显式抛「未实现」错误（Phase 1 保留入口）

### Implementation for User Story 4

- [ ] T053 [US4] 路由策略：backend/app/orchestrator/strategies.py（StrategyResolver：direct/fan_out 展开执行目标列表，pipeline/router 抛 NotImplemented）
- [ ] T054 [US4] 分配服务：backend/app/services/assignment_service.py（幂等校验（需求状态非 created/assigned 则 409）、目标存在与归属校验、按策略建 Run（direct 1 个 / fan_out 每组员 1 个子 Run + parent_run_id）、事务提交 + Redis 事件发布 + Celery 入队）
- [ ] T055 [US4] 分配端点：backend/app/api/v1/requests.py 增加 POST /requests/{id}/assign（202 `{"run_id","status":"queued"}`，仅校验+入队，RT<100ms）
- [ ] T056 [US4] 前端拖拽：frontend/src/components/board/Board.tsx 集成 dnd-kit（RequestCard useDraggable + AgentCard/GroupCard useDroppable），拖拽目标高亮
- [ ] T057 [US4] 前端确认弹窗：frontend/src/components/board/AssignModal.tsx（拖到 Agent 直接确认 / 拖到组显示策略选择，默认组配置，确认后调 assign API）
- [ ] T058 [US4] 前端状态管理：frontend/src/stores/boardStore.ts + frontend/src/hooks/useRequests.ts（assign 乐观更新卡片状态 + 失败回滚 + 错误提示）
- [ ] T059 [US4] 审计挂接：在 backend/app/api/v1/requests.py 的 assign 端点内调用 core/audit.py 的 write_audit（assign，detail 含 assignee_type/assignee_id/strategy）
- [ ] T060 [US4] 集成验证：S4 场景全过（含 SC-002 受理 <3s，重复分配 409，归档目标拒绝）（按 specs/001-core-workflow/quickstart.md S4 执行，结果记入验证记录表）

**Checkpoint**: US1-4 独立可用——「需求到执行的分发闭环」交付

---

## Phase 7: User Story 5 - 单 Agent 执行与实时反馈（Priority: P1）

**Goal**: Celery Worker 驱动 ReAct 循环（LLM+工具），状态机流转、分步日志与成本实时推送（WebSocket）、失败重试/取消/等待补充

**Independent Test**: 提交需求 → 观察实时日志 → 查看结果与制品 → 失败重试（SC-003/004/006/008）

### Tests for User Story 5（先写，先红后绿）⚠️

- [ ] T061 [P] [US5] 状态机单元测试 backend/tests/unit/test_state_machine.py：合法迁移全路径（queued→running→completed/failed/needs_input→running/cancelled/timeout）、非法迁移全部拒绝（终态不可迁移、failed 仅可→queued 且重置 current_step/cost/error）、幂等
- [ ] T062 [P] [US5] 执行器单元测试 backend/tests/unit/test_runner.py（mock LiteLLM）：LLM 直接 final / tool_call→执行→回填→final / max_steps 超限终止 / 超时终止 / 成本熔断（cost>limit 终止） / needs_input 触发 / 异常转可读错误
- [ ] T063 [P] [US5] 运行 API 集成测试 backend/tests/integration/test_runs.py：详情 200（含 steps/cost/result/error/artifacts）/ cancel 200（终态 409）/ retry 202（仅 failed/timeout）/ reply 200（仅 needs_input，回复后回 running）/ 制品下载 302
- [ ] T064 [P] [US5] WS 集成测试 backend/tests/integration/test_ws.py：握手鉴权（无效 token 401）/ status_change、step_new、step_update、log_line、cost_update、run_completed/run_failed/needs_input 事件按序到达 / ping→pong

### Implementation for User Story 5

- [ ] T065 [US5] 状态机：backend/app/orchestrator/state_machine.py（RunStatus 枚举 + 允许迁移表 + transition() 幂等迁移函数 + 时间戳维护 + 终态判定）
- [ ] T066 [US5] 执行器基座：backend/app/executors/base.py（ToolExecutor 协议：name/schema/execute）+ backend/app/executors/llm.py（LiteLLM 封装：chat 调用、tool_call 解析、usage→cost_cents 换算（模型单价表可配置））
- [ ] T067 [US5] HTTP 工具：backend/app/executors/http.py（web_search 工具：调用联网搜索 API 返回结果；其余 Phase 1 内置工具集仅注册名称）
- [ ] T068 [US5] ReAct 执行器：backend/app/orchestrator/runner.py（消息序列循环：LLM→tool_call→executor→回填→再 LLM，直至 final/max_steps/超时/成本上限；每步写 steps 表 + 发布事件 + 累计 cost_cents；needs_input 时挂起等待 reply）
- [ ] T069 [US5] Celery 任务：backend/app/tasks/execute_run.py（execute_run 任务：入队后由 Worker 消费，加 Redis 分布式锁防重复执行）+ backend/app/tasks/events.py（Worker→API 状态回调：publish_run_event 封装各事件类型）
- [ ] T070 [US5] 运行端点：backend/app/api/v1/runs.py（GET /runs/{id}、POST /runs/{id}/cancel、POST /runs/{id}/retry、POST /runs/{id}/reply、GET /runs/{id}/artifacts/{artifact_id} 302 预签名）
- [ ] T071 [US5] WS 端点：backend/app/api/v1/ws.py（WS /ws/runs/{id}：token 鉴权、订阅 Redis 频道 run:{id} 转发事件、连接断开清理、ping/pong 保活）
- [ ] T072 [US5] 前端运行详情：frontend/src/pages/RunDetailPage.tsx + frontend/src/components/run/StepList.tsx（状态流转展示 + 分步展开查看输入输出 + 成本实时显示 + 重试/取消/回复按钮）
- [ ] T073 [US5] 前端 WS 订阅：frontend/src/hooks/useRunSocket.ts（连接管理、自动重连、事件分发到 Zustand/Query、日志 200ms 批量渲染）
- [ ] T074 [US5] 审计挂接：在 backend/app/api/v1/runs.py 内调用 core/audit.py 的 write_audit（run_cancel/run_retry/run_reply）
- [ ] T075 [US5] 成本熔断验证：S5 场景 5（成本实时累计且不超上限，超限自动终止——SC-006），在 backend/tests/unit/test_runner.py 增加 cost_limit 终止断言
- [ ] T076 [US5] 完整闭环集成测试：backend/tests/integration/test_execution_flow.py（mock LLM 走完 分配→入队→执行→完成，断言 steps/result/cost/artifacts 与 WS 事件——SC-004）

**Checkpoint**: US1-5 全部独立可用——「从执行到产出的完整闭环」交付

---

## Phase 8: Polish & 跨切面收尾

**Purpose**: 跨用户故事的横切能力（配额/统计/审计查询/部署/质量门禁）与生产化收尾

- [ ] T077 用户月配额：backend/app/services/quota_service.py（用户月成本累计 + 超月配额熔断，在执行入口校验——宪法「每个用户有月配额」）
- [ ] T078 统计端点与仪表盘：backend/app/api/v1/stats.py（GET /stats/overview：running/queued/completed_today/failed_today/success_rate_7d/cost_today_cents/cost_trend_7d）+ frontend/src/pages/DashboardPage.tsx
- [ ] T079 审计查询：backend/app/api/v1/audit.py（GET /audit-logs，admin 专属，`?user_id=&action=&page=`）+ frontend/src/pages/AdminPage.tsx 完整审计列表
- [ ] T080 [P] 部署完整化：deploy/docker-compose.yml 增加 api/worker/beat/frontend 服务（服务间依赖与健康检查）+ deploy/Caddyfile（HTTPS 反向代理 + WS 支持）
- [ ] T081 [P] 镜像构建：backend/Dockerfile + frontend/Dockerfile（多阶段构建、非 root 运行、密钥不落镜像）
- [ ] T082 [P] 安全收尾：全局日志脱敏复核（Authorization/密钥字段不打印）+ 前端统一错误提示组件（frontend/src/components/ui/）
- [ ] T083 全量质量门禁：ruff check + mypy + pytest 全绿（backend/pyproject.toml 配置的 ruff/mypy，backend/tests/ 全量 pytest）+ eslint + tsc + vitest 全绿（frontend/package.json 脚本），修复全部告警
- [ ] T084 quickstart 验证：按 specs/001-core-workflow/quickstart.md 执行 S1~S5 全部场景（deploy/docker-compose.yml 起全套服务），如实更新 SC 对照表与验证记录表（通过才勾选，未过保持 ⏳）

**Checkpoint**: Phase 1 核心闭环可独立上线——全部 SC 验证记录完成

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，可立即开始
- **Foundational (Phase 2)**: 依赖 Setup 完成——**阻塞全部用户故事**
- **User Stories (Phase 3-7)**: 全部依赖 Phase 2 完成；按 US1→US5 顺序实现（US 间无强数据依赖，但 US2/3/4/5 的验收场景隐含 US1 登录前提，且「每个 Phase 一次提交」要求串行推进）
- **Polish (Phase 8)**: 依赖全部用户故事完成

### User Story Dependencies

- **US1 (P1)**: Phase 2 后即可开始，无其他故事依赖——MVP 范围
- **US2 (P1)**: Phase 2 后即可开始；不依赖 US1 数据，但权限依赖 US1 的 RBAC 依赖注入
- **US3 (P1)**: Phase 2 后即可开始；附件存储依赖 Phase 2 的 MinIO 配置
- **US4 (P1)**: 依赖 US2（Agent/组存在）与 US3（需求存在）——分配对象与内容载体
- **US5 (P1)**: 依赖 US4（Run 由分配产生）；WS 推送依赖 Phase 2 事件总线

### Within Each User Story

- 测试 MUST 先写并 FAIL（红），再实现使其 PASS（绿）
- 实现顺序：schemas → services → endpoints → 前端 → 审计挂接 → 集成验证

### Parallel Opportunities

- Phase 1 的 T003/T004/T005/T006/T007 可并行
- Phase 2 的 T012/T013/T014/T015 可并行（依赖 T008 建表后即可）
- 每个用户故事的测试任务（[P]）可并行
- Phase 8 的 T080/T081/T082 可并行

---

## Parallel Example: User Story 5

```bash
# 先并行写测试（红）：
Task: "状态机单元测试 backend/tests/unit/test_state_machine.py"
Task: "执行器单元测试 backend/tests/unit/test_runner.py"
Task: "运行 API 集成测试 backend/tests/integration/test_runs.py"
Task: "WS 集成测试 backend/tests/integration/test_ws.py"

# 再并行实现独立模块：
Task: "状态机 backend/app/orchestrator/state_machine.py"
Task: "执行器基座 backend/app/executors/llm.py"
```

---

## Implementation Strategy

### MVP First（User Story 1 Only）

1. Phase 1: Setup
2. Phase 2: Foundational（CRITICAL——阻塞所有故事）
3. Phase 3: User Story 1（注册登录与权限）
4. **STOP and VALIDATE**: 独立测试 US1（S1 场景全过）
5. 首个可演示切片：注册/登录/角色权限

### Incremental Delivery

1. Setup + Foundational → 基础设施就绪
2. US1 → 独立测试 → 演示（认证与权限 MVP）
3. US2 → 独立测试 → 演示（Agent 与组管理）
4. US3 → 独立测试 → 演示（需求看板）
5. US4 → 独立测试 → 演示（拖拽分配）
6. US5 → 独立测试 → 演示（执行闭环，完整可用系统！）
7. Polish → 生产化收尾（配额/统计/审计查询/部署/全量门禁）

### 提交节奏（宪法强制）

- Phase 1 结束：`feat(setup): 项目初始化与工程骨架（T001-T007）`
- Phase 2 结束：`feat(foundation): 数据层/安全/事件/队列基础设施（T008-T018）`
- Phase 3 结束：`feat(auth): 注册登录与三角色权限（T019-T028）`
- Phase 4 结束：`feat(agents): Agent 与 Agent 组管理（T029-T040）`
- Phase 5 结束：`feat(requests): 需求创建与看板（T041-T050）`
- Phase 6 结束：`feat(assign): 拖拽分配与任务入队（T051-T060）`
- Phase 7 结束：`feat(execution): 执行引擎与实时反馈（T061-T076）`
- Phase 8 结束：`feat(polish): 配额/统计/部署/质量门禁收尾（T077-T084）`
- 每个 Phase 提交后：勾选 tasks.md 已完成任务并提交 `docs(tasks): 更新 001-core-workflow 任务状态`

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- 每个用户故事独立可完成、可测试（红→绿）
- 测试未过前禁止标记实现任务完成（TDD 纪律）
- 实现偏离 spec/plan/contracts 时：回头改上游文档并写进当次提交信息
- 每个 Phase 结束：质量门禁（lint + 类型检查 + 测试）+ git status 复查 + 一次提交
- 未决项（宪法 Follow-up）：router 智能路由（Phase 2 特性）、代码沙箱 gVisor（Phase 3）、图执行引擎评审——均不在本特性实现范围
