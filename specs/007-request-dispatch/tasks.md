# Tasks: 007-request-dispatch

> TDD 纪律：每个「写实现」任务前有对应的「写测试」任务（红 → 绿）。
> `[P]` = 可并行。每 Phase 一次提交，提交信息记录覆盖的任务编号。
> 既有测试基线：后端 1287 passed / 前端 115 passed——007 后只增不减，
> 被收窄的断言逐条列在 §Notes-3。

## Phase 1：规格（已完成，本轮交付）

- [X] T001 Write `specs/007-request-dispatch/spec.md`：4+2 用户故事、15 FR、7 SC、7 项非目标、6 条假设。
- [X] T002 Write `research.md`（R-01..R-10 + 十项现状实测盘点）。
- [X] T003 [P] Write `data-model.md` 与 `contracts/api.md`（判定顺序 + 行为用例表 + 既有端点变更清单 9 条）。
- [X] T004 [P] Write `checklists/requirements.md`（SC→FR→US 双向映射 + 宪法对齐 + 实测推翻记录）。
- [X] T005 Write `plan.md`（Constitution Check 20 条 + 6 Phase + 风险）。
- [X] T006 修宪法 1.0.0 → 1.1.0（定位纳入「需求分派给员工」，Sync Impact Report；用户已确认）。
- [X] T007 Write `quickstart.md`（s8 八场景验收设计；实测记录留空待 P6）。
- [X] T008 建分支 `feat/007-request-dispatch` + `.specify/feature.json` 指向 007。
- [X] T000（前置审计，开工前完成）回勾 006 tasks.md T006-T027：实现早已合并但勾选漏提交，
  先实跑验证（unit 87 + integration 12 + cost 1 通过）后勾选并单独提交 `f3f6540`。

## Phase 2：数据模型与状态机（TDD，无行为变化，可独立上线）

- [ ] T009 [P] Unit: `backend/tests/unit/test_request_state_machine.py` — 迁移表全量
      （data-model §2 每行：合法/非法/幂等）；不变量三条（assignee 非空 ⇔ pending|accepted 等）。
- [ ] T010 Write `backend/app/orchestrator/request_state_machine.py`：`RequestStatus` 枚举
      （+pending/accepted）、`_TRANSITIONS` 表、`transition()`（仿 run 状态机；非法迁移
      `TransitionError` → 调用方转 409）。
- [ ] T011 Write `backend/alembic/versions/0011_request_dispatch.py`：requests +3 列
      （assignee_id ON DELETE SET NULL / dispatched_at / accepted_at）、枚举
      `ADD VALUE IF NOT EXISTS` ×2、`request_dispatches` 与 `notifications` 两表 +
      索引（含未读部分索引）。无存量回填（新列全可空）。
- [ ] T012 Integration: 迁移后 schema 断言（列/索引/枚举值存在）+ downgrade 冒烟
      （枚举值不删，同 006 纪律）。

## Phase 3：分派闭环（TDD）— 3a 纯增量（新端点），单独提交

- [X] T013 [P] Unit: schemas 校验（`AssigneeType +user`、`RequestStatus +2`、
      `RejectIn` reason 必填 1..2000、`NotificationOut` 字段）。
- [X] T014 [P] Integration: `backend/tests/integration/test_dispatch_actions.py` —
      accept/reject 全用例（AC-1..5 / RJ-1..5）：状态、assignee 清空、通知、历史、审计。
- [X] T015 Write `backend/app/services/dispatch_service.py`：accept / reject / reassign
      纯增量逻辑（历史 append + 通知 + 审计 + 状态机迁移；行级锁 `for_update`）。
- [X] T016 Modify `backend/app/api/v1/requests.py`：新增 `POST /{id}/accept`、
      `POST /{id}/reject` 端点（挂 dispatch_service；判定顺序照 contracts §4/§5）。

## Phase 3：分派闭环 — 3b 改既有（契约变更），单独提交

- [X] T017 [P] Integration: 可见性谓词（A-5 越权 404 / scope=relevant&mine / R-1..R-5）。
- [X] T018 Modify `backend/app/services/request_service.py`：`get_request` 谓词
      owner-or-assignee；`list_requests` 加 scope；**同步更新既有被收窄的测试断言**
      （清单见 Notes-3，逐条在提交信息说明）。
- [X] T019 Modify `backend/app/services/assignment_service.py`：`_ASSIGNABLE_STATUSES`
      +accepted；`assert_user_quota` 入参改 `request.owner_id`（R-03）；user 分支
      （不建 Run 不入队）与 agent/group 分支拆分。
- [X] T020 Modify `backend/app/schemas/request.py` + assign 端点：`assignee_type=user`
      受理 + `note` 参数；user 分支响应 `{"request_id","status":"pending"}`。
- [X] T021 Integration: assign 全用例 A-1..A-10（含并发 A-10/AC-5 双请求断言）。
- [X] T022 Modify `backend/app/core/audit.py`：`REQUEST_DISPATCH_ACTIONS` 四动作
      集中定义（request_dispatch/accept/reject/notification_read）+ 断言测试
      （有操作必有行，SC-003）。

## Phase 4：通知与人员端点（TDD，可独立上线）

- [X] T023 [P] Integration: `GET /users/options`（U-1..4：仅在职、viewer 403、稳定排序）。
- [X] T024 Write `backend/app/api/v1/users.py` + `user_service.list_options()`：在职用户
      最小投影（id/email/role），注册进 main.py。
- [X] T025 [P] Integration: 通知读写（N-1..4：未读过滤、全部已读、他人 id 越权 updated=0）。
- [X] T026 Write `backend/app/api/v1/notifications.py` + service：列表/未读/已读
      （UPDATE 带 user_id 条件）、审计 notification_read 仅记条数。
- [X] T027 Write `publish_request_event()`（`app/core/events.py`）+ `WS /ws/requests/{id}`
      （`api/v1/ws.py`；仅 owner/当前 assignee 可订阅，否则 4401）。
- [X] T028 [P] Modify `backend/app/core/metrics.py`：分派/通知计数器（gauge/counter，
      对齐 003 风格；P4 与功能同提交，不「先上线后补」）。

## Phase 5：前端（每提交过 eslint/prettier/tsc/vitest）

- [X] T029 [P] Unit: `api/endpoints.ts` 新封装 + 类型（fetchUserOptions /
      acceptRequest / rejectRequest / fetchNotifications / markNotificationsRead）。
- [X] T030 Modify `stores/boardStore.ts` + `hooks/useRequests.ts`：scope 参数、
      accept/reject mutation、`?request=` 深链兼容。
- [X] T031 Write `components/board/EmployeeTargets.tsx` + `AssignModal` user 分支
      （落点视觉 + 无策略步骤的直接确认）。
- [X] T032 Modify `pages/BoardPage.tsx`：「待接收」列、三类拖拽目标、范围切换器。
- [X] T033 Write `components/layout/NotificationBell.tsx` + 通知中心面板：
      TanStack Query `refetchInterval` ≤30s、全部已读、未读角标。
- [X] T034 Modify `pages/BoardPage.tsx` 详情弹层：分派信息（当前接收人）+
      经手时间线（dispatch/reassign/accept/reject，来自新详情字段）。
- [X] T035 [P] Unit: 新组件与 store 测试（vitest；含 scope 默认值与越权不渲染）。

## Phase 6：验收与合并

- [ ] T036 Write `deploy/verify/s8_request_dispatch.py`：quickstart §1 八场景
      （A–H），退出码非零即失败。
- [ ] T037 Run 全量门禁（后端 pytest + ruff/format/mypy；前端五件套）+
      s1–s4/strategy 回归（FR-014 零行为变更证据）+ 填 quickstart 实测记录。
- [ ] T038 Update `README.md`：页面表（收件箱/铃铛）、API 概览（新端点 + scope
      默认值 + 配额口径变更）、已知限制（通知仅站内）。
- [ ] T039 Update `docs/architecture.md`：需求状态机图（pending/accepted 分支）+
      分派链路一节（引用 research R-02/R-03）。
- [ ] T040 ff 合并 `feat/007-request-dispatch` → `master`，删分支，
      `git status` 确认干净（宪法：无密钥/临时文件）。

## 依赖与并行

```
P2(T009-T012) → P3a(T013-T016) → P3b(T017-T022) → P4(T023-T028) → P5(T029-T035) → P6
                    3a 与 3b 必须分两次提交（回滚粒度：纯增量先、改既有后）
T023 与 T026 可并行；T029/T035 可并行；T031/T032/T033/T034 同属看板改造，
按 T030→T031→T032 顺序，T033/T034 可与 T032 并行
```

## 风险（对应 plan §5，实现时逐条核对）

1. `GET /requests` 默认 scope 变更 → T018 同步更新断言 + T037 回归。
2. 配额入参改 owner → T021 双边断言（SC-005）。
3. 枚举 ADD VALUE 同事务限制 → T011 无存量回填，单 migration 安全。
4. AssignModal user 落点无策略 → T031 分支渲染，不复用 agent 策略 UI。
5. 轮询 QPS → 仅铃铛独立轻查询；看板数据不轮询。

## Notes（贯穿全清单的硬约束）

1. **行级锁纪律**：accept/reject/dispatch/reassign 一律
   `get_request(..., for_update=True)` 后校验状态——「同时接收/退回」双赢是
   本特性最高优先级并发缺陷（SC-004）。
2. **404 不暴露存在性**：非「owner 或当前 assignee」对需求的一切操作返回 404，
   与既有归属纪律同向；不得返回 403（那等于确认需求存在）。
3. **既有测试断言收窄清单**（T018 逐条改、提交信息说明）：
   - `tests/integration` 中「非 owner 访问需求 → 404」的用例：区分「非 owner 且非
     assignee」（仍 404，保留）与「assignee 访问」（新行为，改断言）；
   - `test_execution_flow.py` / assign 相关用例中「只能分配自己的需求」：
     增加 assignee 可分派的正向用例；
   - `GET /requests` 不传 scope 的用例：补 relevant 默认值断言（A2 契约变更）。
4. **成本恒记 owner**：任何新写入路径不得改 `Run.owner_id` 继承；quota 断言
   只允许出现在 assign 入口且入参为 `request.owner_id`。
5. **执行引擎零改动**：`orchestrator/`（除新增 request_state_machine）、
   `executors/`、`tasks/`、`runs` 端点不动；git diff 出现这些目录即违规。
6. **通知 title 存标题快照**：需求标题可改，通知是当时事实（data-model §4）。
