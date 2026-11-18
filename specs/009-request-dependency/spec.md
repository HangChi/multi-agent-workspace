# Feature Specification: 需求依赖编排（009）

**Feature Branch**: `feat/009-request-dependency`

**Created**: 2026-11-18

**Status**: Draft

**Input**: 用户 2026-11-18 路线图「P1 项目容器」之后的下一个特性，范围经二次确认：
**需求依赖 DAG**——「A 阻塞 B」的有向边 + 硬门禁（依赖未完成不能启动执行）+
完成通知；**拒绝成环、边可增删**。组织形态（项目成员/共享）仍后置。

## User Scenarios & Testing *(mandatory)*

### US1 — 给需求之间连「先后再做」的边（优先级 P1）

作为需求创建者或经手人，我希望在需求详情里给某条需求加上「依赖另一条需求」，
让看板上那堆卡片之间有先后再做的结构，而不是全靠人脑记。

**Why this priority**：边是整个特性的地基——没有边，门禁与通知都无从谈起；
且单独实现它就有价值（能连边、能看到结构、能取消）。

**Independent Test**：建 A、B 两条需求 → 在 B 的详情里添加「依赖 A」→
B 的依赖列表显示 A（含标题与状态）；删除该边后列表为空。不需要 US2/US3。

**Acceptance Scenarios**：

1. **Given** 需求 B 属于我（或分派给我），**When**
   `POST /requests/{B}/dependencies {dependency_id: A}`，**Then** 201 返回
   `{id, request_id, dependency_id, dependency_title, dependency_status}`。
2. **Given** B 已依赖 A，**When** 再次添加同一条边，**Then** 409
   （`UNIQUE(request_id, dependency_id)`，重复边没有第二语义）。
3. **Given** B 依赖 A，**When** `DELETE /requests/{B}/dependencies/{A}`，
   **Then** 204 且 B 的依赖列表不再含 A。
4. **Given** B 依赖 A、A 又依赖 C，**When** 尝试给 C 添加「依赖 B」，
   **Then** 409 CONFLICT（会成环：B→A→C→B）。自依赖（B 依赖 B）→ 400。
5. **Given** 依赖目标不存在，**When** 添加，**Then** 404（不暴露存在性，
   同既有归属纪律）。

### US2 — 前置没完成，拖给 Agent 会被挡下（优先级 P1）

作为想启动执行的人，我希望在依赖未满足时拖需求给 Agent/组被明确拒绝，
并告诉我是被哪几条卡住——而不是拖下去跑出一堆基于错误前提的结果。

**Why this priority**：硬门禁是这个特性对用户的核心承诺；「只连边不拦截」
会让「阻塞」名不副实（用户在范围确认时明确选了硬门禁）。

**Independent Test**：建 A、B，B 依赖 A（A 未完成）→ 拖 B 给 Agent → 409
且消息列出 A；把 A 标完成后同一操作 → 202 建 Run。

**Acceptance Scenarios**：

1. **Given** B 依赖 A 且 A ∈ {created, pending, accepted, assigned, running}，
   **When** 拖 B 给 Agent/组，**Then** 409 CONFLICT，消息形如
   「有 1 个前置需求未完成：A 标题（执行中）」——**逐条列出**，不只说数量。
2. **Given** A 已 completed / failed / cancelled，**When** 拖 B，
   **Then** 202 正常建 Run（门禁看「是否终态」而非「是否成功」，见 FR-006）。
3. **Given** B 有 3 个前置其中 2 个未完成，**When** 拖 B，
   **Then** 409 消息列出那 2 个（完成的那些不出现）。
4. **Given** 定时规则到点自动分配 B 且 B 有未完成前置，**When** 触发发生，
   **Then** 需求**照样创建**（trigger result=fired、reason 注明未分配），
   但不建 Run——不能因为门禁把整次触发记成 failed 连需求一起回滚
   （004 的既有失败路径会丢需求）。

### US3 — 前置完成时通知可以启动了（优先级 P2）

作为等着开工的人，我希望前置需求一终态就收到通知，知道「现在可以启动了」——
而不是每隔半天手动去翻前置的状态。

**Why this priority**：通知是门禁的配套——只拦不通知，用户会被门禁挡住后
困在「不知道什么时候能试」。

**Independent Test**：建 A、B，B 依赖 A；把 A 取消（终态）→ B 的创建者
（与当前接收人，若非同一人）各收到一条 `dependency_ready` 通知，
payload 带前置的终态；再次对 A 做终态操作不重复发（幂等）。

**Acceptance Scenarios**：

1. **Given** B 依赖 A、A 从 running 收敛为 completed，**When** 收敛发生，
   **Then** B 的 owner 收到 `dependency_ready`；若 B 有 assignee 且非 owner，
   其也收到一条；通知 payload 含 `{dependency_title, dependency_status}`。
2. **Given** A 以 failed / cancelled 终态，**When** 收敛发生，**Then** 同样发通知，
   但文案区分「前置已失败/已取消——请确认后手动启动」（前端按 payload.status
   渲染不同措辞，后端同类型不拆三种）。
3. **Given** B 的多个前置同时终态（批量收敛），**When** 收敛发生，
   **Then** 每个前置各发一条（不合并成「都好了」——合并后说不清是哪个好了，
   且payload 要带具体标题）。
4. **Given** B 自己已终态（completed/failed/cancelled），**When** 前置收敛，
   **Then** 不发通知（B 已经不需要启动了）。
5. **Given** B 已在 running，**When** 前置收敛，**Then** 不发通知——
   running 的 B 不可能有未完成前置（门禁保证），防御性跳过。

### US4 — 看板卡片上看得出「被什么卡住」（优先级 P2）

作为扫看板的人，我希望卡片上直接看到「这条需求有未完成前置」，
而不是拖了一次被 409 才知道。

**Why this priority**：门禁的可见性兑现——被挡下的原因要前置可见，
否则用户把 409 当成故障。

**Independent Test**：B 依赖未完成的 A → 看板卡片显示「前置未完成」徽章
（hover 显示数量与标题）；A 终态后徽章消失。

**Acceptance Scenarios**：

1. **Given** B 有未完成前置，**When** 看板渲染 B 的卡片，**Then** 卡片
   元信息行出现前置徽章（图标 + 未完成数量），title 提示逐条标题。
2. **Given** B 的前置全部终态，**When** 渲染，**Then** 不显示徽章
   （「都就绪」不是异常状态，不占视觉空间）。
3. **Given** B 无任何依赖，**When** 渲染，**Then** 不显示徽章。

### Edge Cases

- **成环**：A→B→C→A。加边时做可达性检测（新边会成环 ⇔ 起点可达终点），
  拒绝并说明「会形成循环依赖」。检测用 BFS（单需求维度，数据量小，
  不上递归 CTE——见 research R-05）。
- **自依赖**：B 依赖 B → 400 INVALID_INPUT。
- **重复边**：409（UNIQUE 约束兜底，service 先查给友好错误）。
- **依赖目标不存在**：404。依赖目标可以是**任何人的需求**（内部协作工具
  的定位——「B 等隔壁团队的 A」是最自然用法；标题会展示给连边者，
  见 research R-06 的取舍记录）。
- **删除边**：任何时刻可删（无状态约束）——误连能改；操作者需对
  **被依赖方（B）**有编辑权（owner-or-assignee，同标题/描述）。
- **依赖方终态后**：边自动保留（历史结构可见），不影响任何门禁。
- **定时调度自动分配撞门禁**：见 US2 场景 4——建单不分配，trigger
  result=fired + reason 注明。
- **重试（006）撞门禁**：已在 running 的需求不会再有未完成前置
  （门禁保证），重试不重查依赖（省一次往返，也防「前置在重试间隙
  被删」的奇异状态）。
- **并发收敛**：两个前置同时终态 → 两条通知，无锁竞争（settle 各自事务）。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**：系统 MUST 提供需求间有向依赖边：`request_dependencies` 表
  （`id / request_id / dependency_id / created_by / created_at`，
  `UNIQUE(request_id, dependency_id)`，两个 FK 均 `ON DELETE CASCADE`）。
  语义：`request_id` 依赖 `dependency_id`（后者是前置）。
- **FR-002**：系统 MUST 提供边的增删查：
  `POST /requests/{id}/dependencies`、`GET /requests/{id}/dependencies`、
  `DELETE /requests/{id}/dependencies/{dependency_id}`；
  另提供 `GET /requests/{id}/dependents`（被谁依赖，反向视图）。
- **FR-003**：加边 MUST 拒绝成环（可达性检测，409）与自依赖（400）；
  加边与删边的操作者 MUST 对被依赖方（`request_id` 侧）有编辑权
  （owner-or-assignee，否则 403/404）。
- **FR-004**：分配门禁——`POST /requests/{id}/assign`（拖 Agent/组）与
  定时调度的自动分配 MUST 在校验目标之前检查依赖：存在任一非终态前置 →
  409 CONFLICT 且消息逐条列出（标题 + 当前状态）。终态集合 =
  `completed / failed / cancelled`（timeout 归 failed 语义，见 FR-006）。
- **FR-005**：`POST /requests/{id}/accept`（接收）与分派类操作
  （dispatch/reassign/reject）MUST NOT 依赖检查——责任流转与执行启动
  是两个关注点，员工可以先接收一个还被挡着的需求。
- **FR-006**：门禁判据 MUST 是「前置是否终态」而非「是否成功」——
  failed/cancelled 的前置同样解锁。理由：门禁管顺序不管成败；
  失败前提下的启动决策交给人（通知会说明前置的实际终态）。
- **FR-007**：前置收敛到终态时（`settle_request_status` 的唯一汇聚点），
  系统 MUST 对每个「其余前置均已终态」的后续需求发一条 `dependency_ready`
  通知给其 owner 与 assignee（非同一人时两条，同人去重）；后续需求自身
  已终态或在 running 时跳过（防御性，门禁保证后者不该发生）。
- **FR-008**：通知 MUST 幂等——同一后续需求对同一前置只发一次
  （settle 的 running 门禁天然保证：需求离开 running 后不再收敛）。
- **FR-009**：定时调度（004）自动分配撞门禁时 MUST 建单但不分配：
  trigger `result=fired`、`reason` 注明「前置未完成，未自动分配」，
  需求留在 created 等人工启动——**不能**让门禁把整次触发记 failed
  （004 的失败路径会连需求一起回滚）。
- **FR-010**：`GET /requests/{id}` 详情 MUST 返回依赖概要
  （`dependencies`：每条含 id/标题/状态/是否终态；`blocked`：是否有未完成前置），
  供详情弹层与卡片徽章一次取到（列表端只返回 `blocked` 布尔，
  不返回边明细——避免 N+1）。
- **FR-011**：边的增删 MUST 写审计（`request_dependency_add/remove`，
  detail 带对端 request_id 与标题快照）。
- **FR-012**：前端 MUST 在详情弹层提供依赖管理（列表 + 添加 + 移除），
  需求卡片显示未完成前置徽章（数量 + hover 标题），分配被 409 挡下时
  错误横幅展示逐条阻塞项（既有错误链路，不新造）。
- **FR-013**：本特性 MUST NOT 改需求状态机（不加 blocked 态——门禁是
  分配时的前置校验，不是需求状态）、MUST NOT 改 Run 状态机与重试逻辑、
  MUST NOT 改配额口径、MUST NOT 引入拓扑排序/关键路径/自动级联启动
  （用户 2026-11-18 明确只做硬门禁 + 完成通知）。

### Key Entities

- **依赖边（Request Dependency）**：一条 `(request_id, dependency_id)` 有向边。
  append-only 语义的变体：可增可删（用户确认「误连能改」），但**不更新**——
  改语义就是删旧建新。删除不需要历史留痕（审计日志承担）。
- **需求（Request）**：不改列。详情响应派生出 `dependencies` 概要 +
  `blocked` 布尔（FR-010）。
- **通知（Notification）**：复用 007 的 notifications 表，新增类型
  `dependency_ready`（payload：`{dependency_id, dependency_title,
  dependency_status}`）。
- 不新增：阻塞状态列、拓扑缓存表、依赖历史表。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**：有未完成前置的需求拖给 Agent，100% 被 409 挡下且消息列出
  全部阻塞项（集成测试：1/2/3 个前置三种规模；漏一个就是漏）。
- **SC-002**：前置终态后 ≤1 次 settle 事务内，后续方收到
  `dependency_ready`（真实栈脚本计时：从 A 终态到通知落库 < 2s）；
  重复收敛不重复发（幂等断言）。
- **SC-003**：分配接口 RT 仍 P95 < 100ms——门禁只加一次按
  `request_id` 的索引查询（真实栈脚本计时，同 s8 手法）。
- **SC-004**：成环边 100% 被拒（集成测试：三环、含经过第三条需求的
  传递环；放行一个环就会让门禁死锁整条链）。
- **SC-005**：007/008 行为零回归——s8（7/7）、s9（5/5）、s1–s4（29/29）、
  strategy（3/3）原样重跑全绿；`assign` 对无依赖需求的响应逐字段不变。
- **SC-006**：定时规则撞门禁时需求不丢（集成测试：断言 trigger=fired、
  需求存在、无 Run、reason 注明）。

## Assumptions

- 依赖目标可以是任何用户的需求（不做可见性校验）——内部协作工具的定位，
  「B 等隔壁团队的 A」是最自然用法；代价是连边者能看到对方标题
  （research R-06 记录取舍）。
- 边的管理权在「被依赖方」的 owner-or-assignee（与标题/描述同谓词）——
  经手人是撞门禁的人，允许其删边避免往返；不同于 project_id 的
  仅创建者（那是可见性字段，边不是）。
- 通知受众 = 后续需求的 owner + assignee（去重）——「可以启动了」这句话
  对经手人同样重要（他才是拖给 Agent 的人）。
- `timeout` 归入终态（与 failed 同）：超时的前置不会永远挡路。
- 边的删除不需要通知对方——删边是低频修正动作，且审计已留痕；
  若将来出现「删边抵掉门禁」的协作摩擦，再加通知（YAGNI）。
- 依赖不在列表端返回明细（FR-010）：看板列表一次 N 条，逐条查边是 N+1；
  卡片的「被卡住」徽章用 `blocked` 布尔 + 详情弹层按需展开。
