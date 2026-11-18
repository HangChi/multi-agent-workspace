# Research: 007-request-dispatch 关键决策记录

> 每条 = 决策 / 依据（含 2026-11-18 对现有代码的实测事实）/ 被否决的方案及理由。
> 被否决的方案比决策本身更有价值——它们记录了「为什么更简单的做法不行」。

## R-01：需求状态加 `pending` / `accepted`，不复用 `assigned`

- **决策**：`request_status` 枚举新增 `pending`（待接收）、`accepted`（已接收），
  形成 `created → pending → accepted → assigned → running → …` 链。
- **依据（实测）**：现状 `assigned` 的语义是「已拖给 Agent、即将/正在执行」
  （`assignment_service.assign()` 末尾 `request.status = "running"`，看板列
  「已分配」介于 created 与 running 之间，仅作为入队瞬态）。把「已分派给员工」
  塞进 `assigned` 会让看板筛选说谎——用户按「已分配」筛出一堆其实还没人认领的需求。
  这与 006 R-03（`retrying` 独立成态）是同一条纪律：**复用现有状态会让下游筛选
  与统计失真时，就必须开新状态**。
- **被否决**：复用 `assigned` 表示「已分派给人」——看板四列语义混淆；
  `created` 加布尔字段 `dispatched`——状态机退化成「状态 + 标志位」双轨，
  迁移校验无处安放（FR-002 要求集中校验）。

## R-02：访问权谓词「owner 或当前 assignee」，且与行级锁链路一致

- **决策**：需求可见/可操作范围从 `owner_id == me` 扩展为
  `owner_id == me OR (assignee_id == me AND assignee 未清空)`；分派/接收/退回/
  转交一律走 `get_request(..., for_update=True)` 行级锁，与 assign 现有并发防护同构。
- **依据（实测）**：`request_service.get_request()` 与 `list_requests()` 都以
  `owner_id == user_id` 为唯一条件；`assign()` 用 `for_update=True` 防并发重复分配
  （注释明言「两个并发请求会同时读到 created，各自建一组 Run」）。若只改读不改锁，
  「同时接收/退回」会双赢（US6 最后一条场景直接失败）。
- **被否决**：
  - 仅放开 assignee 的 assign 权、详情仍限 owner——接收人连需求内容都看不到，
    「接收」失去意义；
  - 分派时把需求复制一份给接收人（owner 变更）——Run/附件/历史全部要迁移或分裂，
    违背 004「规则是以后怎么触发，需求是已经发生过的事」的不可变纪律；
  - 用独立授权表（request_grants）——为一对一关系引入多对多基建，纯过度设计。

## R-03：执行成本与月配额记 `owner`，不记操作人

- **决策**：员工执行分派来的需求，`assert_user_quota` 与成本统计按
  `request.owner_id` 计，与 `Run.owner_id = Request.owner_id` 的既有继承一致。
- **依据（实测）**：`assignment_service.assign()` 第 161-163 行
  `assert_user_quota(db, user_id)` 用的是**操作人**；`_build_run()` 第 79 行
  `owner_id=request.owner_id` 用的是**需求归属**。现状二者恒等所以无感；
  分派后操作人 ≠ owner，若按操作人扣配额，**员工的月配额会被别人派来的活吃爆**，
  而预算归属在真实公司场景里跟需求方走。只改 quota 一个点，Run/统计/熔断全链路
  天然继承 owner 口径，改动面最小。
- **被否决**：记操作人（执行人）——配额误伤，且与 Run.owner_id 双口径分裂；
  双边各记一半——配额可被「拆单」绕过（两个半额加起来超限），熔断出现漏洞。

## R-04：退回 = 回 `created` + 清 assignee，原因必填

- **决策**：`reject` 把需求打回 `created`、清空 `assignee_id`/`dispatched_at`/
  `accepted_at`，写一条含原因的历史记录，并通知分派人。
- **依据**：分派人可能是 owner 也可能只是中间经手人（转交链），打回「分派人手里」
  需要再定义一层 pending 归属，状态空间爆炸；打回 `created` 让 owner 重新决策
  （自己改、重新分派、取消）是唯一闭环解。原因必填是为了让「谁为什么退」可追溯
  （宪法：敏感操作可审计）。
- **被否决**：reject 后回到分派人处 `pending`——经手链 A→B→C 时「回哪一环」
  歧义，且 C 拒收不该让 B 被动接活；允许空原因退回——历史里留一堆
  「已退回」却查不出理由的记录，审计价值减半。

## R-05：人员选择端点最小暴露（`GET /users/options`）

- **决策**：新增只读端点，返回在职用户（`status=active`）的
  `id / email / role` 三元组；member 与 admin 可用，viewer 403。
- **依据（实测）**：路由清单（`app/main.py:80-95`）**没有任何 users 路由**——
  前端要把需求拖到「员工卡片」上，首先得能拿到员工列表。管理后台（003）只有
  审计日志端点，不能复用。暴露面遵循最小化：角色是既有字段且前端需要区分
  （admin/member/viewer 的可见操作不同），不做分页（公司规模量级，
  与 agents/groups 的 `all=true` 拖拽目标集合同构）。
- **被否决**：复用/扩展 admin 端点——viewer 403 且返回字段含邀请码等敏感列；
  前端硬编码员工列表——无 API 即无真相，禁用账号（US6）无法反映；
  带分页的完整 users CRUD——本期不需要用户管理，做了就是范围膨胀（§2.2）。

## R-06：通知落库 + 未读数轮询，request 状态走 WS 复用

- **决策**：通知写 `notifications` 表；顶栏未读数用前端轮询（≤30s，TanStack Query
  `refetchInterval`）；需求状态变更（pending/accepted/…）通过 WS 推送，
  频道方案见 contracts。
- **依据（实测）**：现有 WS 是**按 Run 订阅**的（`publish_run_event(run_id, …)` →
  频道 `run:{id}`，`/ws/runs/{id}?token=`），没有用户级频道；分派事件在 Run 存在
  之前发生，塞不进 run 频道。为通知单独铺一条用户级 WS 长连接（在线状态、多标签
  页去重、重连补发）成本远超收益；轮询未读数是「铃铛小红点」这一强度需求的
  标准解。需求状态变更本身要驱动看板实时刷新，与 001 的 WS 基建同源，
  复用事件总线即可（contracts 定义 request 频道）。
- **被否决**：用户级 WS 频道（`user:{id}` 广播通知）——需引入在线/去重/补发机制，
  违反「简洁高效」；SSE——与现有 WS 双轨并存，维护面 +1；纯轮询全量需求列表——
  看板实时性回退，SC-001/SC-007 达不到。

## R-07：分派统一走 `POST /requests/{id}/assign`，扩展 `assignee_type=user`

- **决策**：拖拽落点从二类（agent/group）扩为三类（+user）；`user` 落点只转交
  责任（建历史 + 通知 + 状态迁移），**不建 Run、不入队**；首次分派与转交同入口，
  由服务端按当前状态区分（无 assignee → 分派；有 assignee → 转交）。
- **依据（实测）**：前端拖拽落点已是一套基建（`AssignTargets.tsx` 从
  `fetchAgents({all:true})` + `fetchGroups({all:true})` 构建目标集，落点弹窗
  `AssignModal` 已处理「选策略」），加第三类目标是增量；`assign` 端点已有
  202/409/404/400 的完整错误语义与审计，扩枚举比新开端点便宜。
- **被否决**：新端点 `POST /requests/{id}/dispatch`——两套落点/弹窗/审计逻辑，
  前端拖拽目标注册表要分叉，「拖到人」和「拖到 agent」在两个代码路径里漂移；
  转交单独端点 `/reassign`——与 dispatch 动作完全相同（都是换接收人），
  纯冗余 API 面。

## R-08：分派历史用独立追加表 `request_dispatches`

- **决策**：新建 `request_dispatches`（append-only）：`request_id / action
(assign|reassign|accept|reject) / actor_id / from_assignee_id / to_assignee_id /
reason / created_at`。
- **依据**：「谁经手过、为什么退」是 US1/US2 的核心验收内容，也是审计的查询面；
  独立表可索引、可 JOIN、可按 action 统计。JSON 字段挂在 request 上则
  「按经手人找我参与的需求」这类查询要扫全表 jsonb。
- **被否决**：`requests.dispatch_history` JSON 列——查询与索引双输；
  只写审计日志不建历史表——audit_logs 是全局动作流，「这条需求的经手链」
  要按 target_id 过滤再排序，语义上是顺带而非一等公民；
  可变更的历史表（允许 UPDATE）——历史必须不可变，与 004「改调度不改写历史」
  同纪律。

## R-09：Request 状态迁移集中到 `request_state_machine.py`

- **决策**：仿 `orchestrator/state_machine.py`（Run 的）建 Request 的迁移表 +
  `transition()`，分派/接收/退回/assign-to-agent 全部经由它；非法迁移抛
  `TransitionError` → 409。
- **依据（实测）**：现状 Request 状态是**裸赋值**（`request.status = "running"`
  散落在 assignment_service，无校验）；006 之前 Run 也是裸赋值，加 retrying 时
  补了状态机才堵住「retrying → completed 跳过执行」这类洞（006 research R-03）。
  Request 状态从 6 个扩到 8 个、操作方从 1 类扩到 3 类（owner/assignee/agent 落点），
  裸赋值必然漏校验。
- **被否决**：继续裸赋值 + 在各端点 if 判断——校验逻辑散落，US6 的边界
  （终态拒收）要在四个端点各写一遍，漏一个就是一个洞；
  把 Request 状态并入 Run 状态机——二者生命周期不同（Request 跨多次 Run/重试），
  合并会让「需求已完成但还有 retrying 的 Run」无法表达。

## R-10：通知表按 (user_id, read_at) 索引，未读数用 COUNT 查询

- **决策**：`notifications(user_id, type, request_id, title, payload jsonb,
read_at null, created_at)`；未读 = `read_at IS NULL` 计数；「全部已读」
批量 UPDATE … WHERE user_id AND read_at IS NULL。
- **依据**：未读计数是顶栏铃铛的唯一数据源（SC-007），QPS 低（轮询 ≤30s/人），
  单表 COUNT 足够，无需物化计数列（多一个要维护一致性的写入点）。
- **被否决**：物化 `unread_count` 列——读写都要维护，通知量小时纯负担；
  通知也走 Redis Pub/Sub 不落库——刷新即丢失，「回顾谁给我派过活」（US5）
  无数据可查；通知带 TTL 自动清理——审计相关动作的通知需可追溯，与 R-08 同纪律。

## 实测校准记录（写规格前的现状盘点，2026-11-18）

| 测量项 | 实测结果 | 对规格的影响 |
|---|---|---|
| users 路由 | `app/main.py` 无 users 路由 | FR-012 必须新端点（R-05） |
| 需求列表过滤 | `list_requests` 条件恒为 `owner_id == user_id` | FR-007 默认范围变更是契约变更，需逐条更新既有测试 |
| assign 归属校验 | `get_request(..., user_id, for_update=True)` | R-02 谓词扩展 + 保留行级锁 |
| 配额扣减方 | `assert_user_quota(db, user_id)`=操作人 | R-03 改按 owner，需集成测试双边断言 |
| Run 归属继承 | `Run.owner_id = Request.owner_id` | 佐证 R-03 口径一致性 |
| 可分配状态集 | `_ASSIGNABLE_STATUSES = {created, assigned}` | pending/accepted 加入可分配集合 |
| Request 状态写入方式 | 裸赋值，无状态机文件 | R-09 |
| WS 频道模型 | 仅 `run:{id}`，无用户级频道 | R-06 |
| 前端拖拽目标源 | `fetchAgents({all})` + `fetchGroups({all})` | R-05/R-07 增员工作为第三类目标 |
| 审计动作组织 | 按特性分组 frozenset + 两处内联（request_create/assign） | 007 集中定义 REQUEST_DISPATCH_ACTIONS |
