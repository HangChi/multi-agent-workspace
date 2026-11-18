# Contracts: 007-request-dispatch API 与事件契约

> 每节先给**判定顺序**（顺序本身就是契约：换序会让同一个请求在不同竞态下得到不同结果），
> 再给必须有的行为用例表。所有端点前缀 `/api/v1`，Bearer 鉴权，错误码沿用
> 既有 `AppError` 体系（`NOT_FOUND`/`INVALID_INPUT`/`CONFLICT`/`FORBIDDEN`/`RATE_LIMITED`）。

## 1. `GET /users/options` — 人员选择（新）

**判定顺序**：鉴权 → 角色（member/admin；viewer → 403）→ 查询。

- Response `200`：`{"items": [{"id": "uuid", "email": "str", "role": "admin|member|viewer"}]}`
- 只返回 `status = active` 的用户；按邮箱排序（稳定输出，便于测试断言）。
- 不分页（公司量级；与 agents/groups 拖拽目标集合同构）。
- viewer 403（与「创建需求」同权：viewer 本就不能建需求，自然也不该选人分派）。

**行为用例**：

| # | 场景 | 期望 |
|---|---|---|
| U-1 | member 调用 | 200，仅含在职用户 |
| U-2 | 存在 disabled 用户 | 不出现在 items |
| U-3 | viewer 调用 | 403 |
| U-4 | 未登录 | 401 |

## 2. `GET /requests` — 范围参数 `scope`（契约变更）

**判定顺序**：鉴权 → 角色（member/admin；viewer 403）→ 构造范围条件 → 过滤/分页。

- 新参数 `scope`：`relevant`（**默认**，我创建的 + 分派给我的）｜`mine`（仅我创建的，
  与 006 及以前完全一致）。
- 其余参数（`status`/`q`/`priority`/`page`/`page_size`）不变；
  `status` 过滤新增 `pending`/`accepted` 两个合法值。
- **默认值变更即契约变更**：旧客户端不传 `scope` 时，从「只看自己的」变为
  「自己的 + 派给我的」。这是有意的（spec Assumptions 2：只扩大不缩小），
  quickstart 必须实测「不传 scope 时他人分派给我的需求出现」。

**行为用例**：

| # | 场景 | 期望 |
|---|---|---|
| R-1 | 不传 scope，有分派给我的 pending 需求 | items 含该需求 |
| R-2 | `scope=mine` | 与 006 行为逐字段一致（回归测试钉住） |
| R-3 | `scope=relevant` + 别人的需求（未分派给我） | 不含 |
| R-4 | `status=pending` | 仅返回待接收（两scope均适用） |
| R-5 | 分派给我的需求已 accepted 并被经手人拖入执行 | 仍在我 relevant 列表（FR-007：终态前一直可见） |

## 3. `POST /requests/{id}/assign` — 扩展 `assignee_type=user`（拖到员工）

**判定顺序**（顺序即契约，逐条中断）：

1. 鉴权 + member/admin；
2. `get_request(for_update=True)`：不存在**或非「创建者/当前接收人」→ `404`（不区分，不暴露存在性）；
3. 月配额：`assert_user_quota(db, **request.owner_id**)` → 超限 `429`
   （注意入参从操作人改为 owner，见 research R-03）；
4. `assignee_type == "user"` 分支（**不建 Run、不入队、不发 run 事件**）：
   a. 目标用户存在且 `status=active` → 否则 `404`；
   b. 状态机迁移：`created|accepted → pending`（换接收人）/ `pending → pending`
      （转交，记 reassign）/ 其余状态（含 `assigned` 及以后）→ `409 CONFLICT`；
   c. 自派允许（assignee == owner → 正常走 pending）；
   d. 写 `request_dispatches`（action=dispatch 或 reassign）+ 更新 requests 三列；
   e. 给新接收人写 `request_dispatched` 通知；转交时给旧接收人写 `request_reassigned`；
   f. 写审计 `request_dispatch`；
5. `assignee_type` 为 `agent|group` 分支：**完全走 001 既有逻辑**，仅两处变化——
   准入状态集合加入 `accepted`（`{created, assigned, accepted}`，见 §7 变更清单），
   配额入参改 owner；
6. 提交后：user 分支发 request 事件（§8 WS）；agent/group 分支照旧 dispatch。

- Request body：`{"assignee_type": "user", "assignee_id": "uuid", "note": "str?"}`
  （note 可选，随历史与通知 payload 落库；agent/group 分支忽略 note）。
- Response `202`：user 分支 `{"request_id": "...", "status": "pending"}`（无 run_id）；
  agent/group 分支保持 `{"run_id": "...", "status": "queued"}` **不变**。

**行为用例**：

| # | 场景 | 期望 |
|---|---|---|
| A-1 | owner 拖给在职员工 | 202 pending；对方 relevant 列表可见；未读 +1 |
| A-2 | pending 状态再拖给另一人 | 202 pending；历史 dispatch+reassign 两条；旧接收人收到 reassigned 通知 |
| A-3 | 拖给 disabled 用户 | 404 |
| A-4 | 拖给自己 | 202 pending（自派） |
| A-5 | 非 owner/非 assignee 拖 | 404，无任何写入 |
| A-6 | running/终态需求拖人 | 409，状态不变，无历史无通知 |
| A-7 | owner 月配额已满，拖给别人 | 429，不建任何记录（FR-009：记 owner） |
| A-8 | 员工 A 把 accepted 的需求拖给自己组的 agent | 202 queued，Run 创建；request.owner 配额扣减（非 A） |
| A-9 | 未 accept 直接拖 agent | 409（提示先接收），需求状态不变 |
| A-10 | 两个客户端同时对 pending 需求 accept/reject | 仅一个 202，另一个 409（行级锁，同 001 并发纪律）。**dispatch 并发除外**：pending 上再拖人的语义就是转交（US1 第二场景），两条并发 dispatch 的正确结果是都 202——一条 dispatch + 一条 reassign，行级锁保证无丢失更新（经手链按每需求单调 seq 排序，不用 created_at：PG 的 now() 是事务开始时间，锁等待会让后插入者持有更早时间戳） |

## 4. `POST /requests/{id}/accept` — 接收（新）

**判定顺序**：鉴权 → 404（不存在或非创建者/当前接收人）→ 状态机
`pending → accepted`（否则 409）→ 历史（action=accept）+ `accepted_at` → 通知分派人
`request_accepted` → 审计 `request_accept` → 提交 → WS 事件。

- body：无（或空 JSON）。Response `202 {"request_id","status":"accepted"}`。
- **仅当前 assignee 可 accept**；owner 不能代接收（「员工接收」是责任确认动作，
  代接收会让经手链失真）。owner 有需要时可直接把需求拖给 agent（A-8 路径）或退回。

**行为用例**：

| # | 场景 | 期望 |
|---|---|---|
| AC-1 | 当前 assignee accept | 202 accepted；分派人收到通知 |
| AC-2 | owner accept 别人派给自己的需求（自派场景） | 202（自派后 assignee==owner） |
| AC-3 | 非 assignee accept | 404 |
| AC-4 | 已 accepted 再 accept | 409 |
| AC-5 | 并发 accept 同一 pending | 一个 202 一个 409；历史仅一条 accept |

## 5. `POST /requests/{id}/reject` — 退回（新）

**判定顺序**：鉴权 → 404 → body 校验（`reason` 必填 1..2000 字符，否则 422）→
状态机 `pending|accepted → created`（否则 409）→ 清 assignee 三列 + 历史
（action=reject, reason 落库）→ 通知分派人 `request_rejected`（payload 含原因）→
审计 `request_reject` → 提交 → WS 事件。

- 权限：**当前 assignee 或 owner**（owner 撤回分派：分派错了要能撤）。
- Response `202 {"request_id","status":"created"}`。

**行为用例**：

| # | 场景 | 期望 |
|---|---|---|
| RJ-1 | assignee 附原因退回 | 202 created；assignee 清空；分派人收到含原因通知 |
| RJ-2 | owner 撤回分派 | 202 created；通知发给被撤接收人 |
| RJ-3 | reason 缺失/超 2000 字符 | 422 |
| RJ-4 | 已 running 的需求 reject | 409 |
| RJ-5 | reject 后再 dispatch | 按首次分派记 dispatch（不是 reassign） |

## 6. 通知（新）

### `GET /notifications?unread_only=&page=&page_size=`

- 判定顺序：鉴权 → 查自己的（`user_id = me`，按 `created_at DESC`）→ 分页。
- Response item：`{"id","type","request_id","title","payload","read_at","created_at"}`。

### `POST /notifications/read`

- body：`{"ids": ["uuid", ...]}`（空数组 = 全部已读）。
- 判定顺序：鉴权 → `UPDATE … WHERE user_id = me AND id IN (…)`（**带 user_id 条件**，
  防越权已读他人）→ 返回 `{"updated": n}` → 审计 `notification_read`（仅记录条数，
  不记录 id 列表，避免审计表膨胀）。
- `updated` 为真实生效行数（已读的再点一次不重复计数）。

**行为用例**：

| # | 场景 | 期望 |
|---|---|---|
| N-1 | 有新分派通知 | `unread_only=true` 含该条；`read_at` 为 null |
| N-2 | 全部已读后 | 未读列表空；再点 read 返回 updated=0 |
| N-3 | 传他人通知 id | updated=0（不越权） |
| N-4 | 分派→接收→退回全链路 | 三方各收到对应类型通知，顺序正确 |

## 7. 对既有端点的变更清单（逐条列，防漏改）

| 端点/文件 | 变更 | 原因 |
|---|---|---|
| `GET /requests` | 默认 scope `relevant`；status 枚举 +2 | FR-007 |
| `POST /requests/{id}/assign` | `assignee_type` +`user`；agent/group 分支准入状态 +`accepted`；配额入参改 owner | FR-001/003/009 |
| `GET /requests/{id}` | 响应体加 `assignee`（`{id,email}|null`）+ `dispatch_history`（最近 N 条） |
| `GET /runs/{id}`（及 cancel/retry/reply/artifacts/WS 订阅） | 归属谓词扩展为「Run owner 或需求当前 assignee」（FR-016）——执行人 ≠ Run.owner_id，经手人必须能查看进度与回复 | US1/US2 详情可见 |
| `schemas/request.py` | `RequestStatus` +2；`AssigneeType` +`user`；新 schema `AcceptOut/RejectIn/NotificationOut…` | — |
| `services/request_service.py` | `get_request` 谓词 owner-or-assignee；`list_requests` scope | FR-007/008 |
| `services/assignment_service.py` | `_ASSIGNABLE_STATUSES` +`accepted`；`assert_user_quota(owner)`；拆出 user 分支（不建 Run） | R-03/R-07 |
| `core/audit.py` | 新增 `REQUEST_DISPATCH_ACTIONS = {request_dispatch, request_accept, request_reject, notification_read}` | FR-013 |
| `core/events.py` | 新增 `publish_request_event(request_id, payload)`，频道 `request:{id}` | §8 |
| `api/v1/ws.py` | 新增 `/ws/requests/{id}`（复用鉴权与心跳） | §8 |

**不改**：`POST /requests`、附件、`runs/*`、`stats/*`、`schedules/*`、
`orchestrator/*`、`executors/*`、`tasks/*`（执行引擎零改动，FR-014）。

## 8. WS 事件（新频道 `request:{id}`）

- 连接：`WS /ws/requests/{id}?token=…`（与 `/ws/runs/{id}` 同鉴权、同心跳、
  同终态关闭策略；额外约束：仅 owner/当前 assignee 可订阅，其他人 4401 断开）。
- 事件 payload（JSON）：

| type | 触发 | 字段 |
|---|---|---|
| `status_change` | 本特性全部分派类迁移 | `{status, assignee_id, actor_id}` |
| `notification` | 发给自己的通知产生 | `{notification_id, type, title}`（前端可触发铃铛刷新） |

- Run 频道（`run:{id}`）不变；看板「执行中」列的实时性仍由 run 事件驱动。

## 9. 审计动作（集中定义，仿 004/005 分组）

```
request_dispatch   分派/转交（detail: to_assignee_id, action=dispatch|reassign, note?）
request_accept     接收
request_reject     退回（detail: reason）
notification_read  已读（detail: count）
```

宪法要求「敏感操作 100% 审计」：以上四个动作 + 既有 `assign`（agent/group）
构成 007 全景；SC-003 逐条断言有操作必有行。

## 10. 错误码速查（007 面）

| 码 | 场景 |
|---|---|
| 404 | **007 新分派端点**（accept/reject/dispatch-user）：需求不存在或非「创建者/当前接收人」；目标用户不存在/禁用 |
| 403 | **既有需求端点**（GET 详情/附件/assign-to-agent）非相关人员——延续 006 锁定契约（test_requests.py），FR-014 零变化 |
| 409 | 状态不允许（执行中/终态拒分派、重复 accept、未 accept 拖 agent、并发接收/退回败者） |
| 422 | body 校验失败（reason 缺失/超长、assignee_type 非法） |
| 429 | 需求 owner 月配额超限 |
| 403 | viewer 越权（users/options、建需求、分派） |

**403 与 404 并存的说明**：这是历史形成的差异（agent/组与 007 新端点用 404，
既有需求端点用 403），统一收敛不在 007 范围——改了就是动 006 锁定的契约，
违反 FR-014。spec US6/FR-008 已按此口径记录。
