# Feature Specification: 项目共享与成员（010）

**Feature Branch**: `feat/010-project-sharing`

**Created**: 2026-11-18

**Status**: Draft

**Input**: 用户 2026-11-18 路线图「P2 按实际组织形态按需添加」的落地。
范围经二次确认：**项目成员/共享**——owner（创建者，全权）+ member（项目内读写协作），
成员可见彼此项目与其中的需求。部门树形组织、看板临时分组不做（留后续按需）。

## User Scenarios & Testing *(mandatory)*

### US1 — 把同事加进项目（优先级 P1）

作为项目创建者，我希望把同事加为项目成员，让这个项目从「我一个人的篮子」
变成「我们几个人的工作区」。

**Why this priority**：成员关系是整个特性的地基——没有它，可见性扩展与协作
权限都无从谈起；且单独实现它就有价值（能加人、能看到谁在项目里）。

**Independent Test**：建项目 → 加 2 个成员 → 成员列表显示 3 人 → 移除 1 人后
列表剩 2 人。不需要 US2/US3。

**Acceptance Scenarios**：

1. **Given** 我是项目 owner，**When** `POST /projects/{id}/members`
   `{user_id: 同事}`，**Then** 201，成员列表含该同事。
2. **Given** 该同事已是成员，**When** 重复添加，**Then** 409
   （`UNIQUE(project_id, user_id)`）。
3. **Given** 我是项目 owner，**When** `DELETE /projects/{id}/members/{user_id}`，
   **Then** 204；该同事从此看不到这个项目及其需求（可见性立即收紧）。
4. **Given** 我是项目 owner，**When** 尝试移除自己，**Then** 409
   （无归属人的项目是脏状态——创建者要退出请删项目；所有权移交是后续特性）。
5. **Given** 我是项目成员（非 owner），**When** 尝试加人/踢人，**Then** 403
   （成员管理是 owner 特权，Q2 确认的两层模型）。

### US2 — 成员看到共享项目与其中的需求（优先级 P1）

作为被加进项目的成员，我希望在看板与项目列表里直接看到这个项目和它的需求，
而不是收一条「你被加了但什么都看不到」的通知。

**Why this priority**：可见性是共享的价值兑现——加了人却看不到活，
共享就是空的；与 US1 合起来才是「拉人进场」的完整闭环。

**Independent Test**：owner 建项目并归类 3 条需求 → 加成员 → 成员登录，
`GET /projects?scope=relevant` 含该项目、`GET /requests` 含这 3 条、
`GET /projects/{id}` 与 `GET /requests/{id}` 均 200（此前是 404/403）。

**Acceptance Scenarios**：

1. **Given** 我是项目成员，**When** `GET /projects?scope=relevant`，
   **Then** 列表含该项目（带 `member_count` 与 `request_count`）。
2. **Given** 项目内有成员创建的 2 条 + owner 创建的 1 条需求，
   **When** 成员 `GET /requests`，**Then** 3 条都在（scope=relevant 口径
   扩展：owner ∪ assignee ∪ 项目成员）。
3. **Given** 我是项目成员，**When** `GET /requests/{某条项目内需求}`，
   **Then** 200（此前非相关人员 403）。
4. **Given** 我已不是成员（被移除），**When** 再访问该项目/其需求，
   **Then** 404（收紧立即可见——不等缓存过期）。

### US3 — 成员在共享项目里协作（优先级 P2）

作为项目成员，我希望直接在共享项目里建需求、改需求、拖给 Agent、连依赖边——
分担这个项目的活，而不是只能看。

**Why this priority**：读写协作是「看得见摸得着」的兑现；只读共享会让协作
断档（Q3 确认成员可见且可协作）。

**Independent Test**：成员对项目内需求：建一条归属该项目的需求（201）→
改标题（200）→ 拖给 Agent（202）→ 连依赖边（201）；对项目外的需求
仍维持 007 谓词（他人非分派需求 403/404）。

**Acceptance Scenarios**：

1. **Given** 我是项目成员，**When** 建需求时选该项目，**Then** 201
   （项目校验从「本人名下」扩展为「我是 owner 或成员」）。
2. **Given** 项目内有他人创建的需求，**When** 成员改其标题/描述/优先级，
   **Then** 200（谓词扩展：owner ∪ assignee ∪ 项目成员）。
3. **Given** 项目内有他人创建的需求，**When** 成员拖它给 Agent/组，
   **Then** 202 建 Run（009 门禁照查——依赖与共享是两个正交维度）。
4. **Given** 项目内有他人创建的需求，**When** 成员改其 `project_id`，
   **Then** 403（归属变更仍仅创建者或该项目 owner——防静默丢活，
   008 R-05 在共享下的收敛解，见 research R-03）。
5. **Given** 成员对项目内需求连依赖边/删边，**When** 目标需求存在，
   **Then** 201/204（009 边端点谓词同样扩展）。
6. **Given** 我是项目成员，**When** `PATCH /projects/{id}`（改名/归档）
   或 `DELETE`，**Then** 403（owner 特权，成员管理类动作成员一律不能做）。

### US4 — 成员变动有通知（优先级 P2）

作为被加进/移出项目的人，我希望收到一条通知说明发生了什么——
否则需求会「忽隐忽现」，完全无法解释。

**Why this priority**：通知是可见性变化的解释机制——US2/US3 改变了成员
能看到的世界，没有通知，用户只会以为系统出了 bug。

**Independent Test**：owner 加成员 → 成员铃铛收到「你已被加入项目 X」；
owner 移除成员 → 被移出者收到「你已被移出项目 X」。

**Acceptance Scenarios**：

1. **Given** owner 添加成员，**When** 添加成功，**Then** 被加者收到
   `project_member_added`（payload 带项目名）。
2. **Given** owner 移除成员，**When** 移除成功，**Then** 被移出者收到
   `project_member_removed`（payload 带项目名）。
3. **Given** 添加的用户不存在/已停用，**When** 添加，**Then** 404/400
   （不停用的用户才能进项目——与分派选人的在职口径一致）。

### Edge Cases

- **owner 删项目**：成员行级联删；成员收到 removed 通知吗？——项目没了，
  通知里点进去 404，反而是噪声。**不发**（与 004 删调度不发通知同纪律：
  目标已不存在时通知无意义）。
- **成员被删用户级联**：用户删除 → 其成员行 CASCADE；其创建的需求
  owner_id 仍在（requests.owner_id 无级联——需求是已发生的事）。
- **停用用户**：已停用用户不能被加为成员（400）；已是成员的用户被停用——
  本特性不级联移除（停用是账号态不是成员态；其登录已被 001 拦住，
  需求归属不变）。留记录，不静默撕合同。
- **共享项目里的需求改归属**：创建者或**该项目 owner** 可改（R-03）；
  其他成员 403。
- **项目归档后成员**：成员关系保留（归档只拦新建不拦成员）；已归档项目
  不能被新建需求选中（008 纪律不变，含成员建的）。
- **重复添加/移除不存在的关系**：409 / 404。
- **成员看到的 request_count**：= 该项目下按调用人可见口径的需求数
  （owner ∪ assignee ∪ 成员）——成员视角下就是全部（R-06）。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**：系统 MUST 提供项目成员关系：`project_members` 表
  （`project_id / user_id / created_at`，`UNIQUE(project_id, user_id)`，
  两个 FK 均 `ON DELETE CASCADE`）。**不设 role 列**——成员身份即角色，
  owner 恒为 `projects.owner_id`（R-01）。
- **FR-002**：系统 MUST 提供成员管理端点：`POST /projects/{id}/members`、
  `GET /projects/{id}/members`、`DELETE /projects/{id}/members/{user_id}`，
  写操作仅 project owner（成员 403、他人 404）；owner 不能移除自己（409）。
- **FR-003**：被加为成员的用户 MUST 收到 `project_member_added` 通知；
  被移除者 MUST 收到 `project_member_removed`（payload 带项目名）；
  **删项目不发**（目标不存在，通知无意义）。
- **FR-004**：`GET /projects?scope=relevant` 的口径 MUST 扩展为
  「我创建的 ∪ 我是成员的 ∪ 派给我需求所属的」；`GET /projects/{id}`
  与 `GET /projects/{id}/members` MUST 对成员开放（此前 404）。
- **FR-005**：`GET /requests?scope=relevant` 的口径 MUST 扩展为
  「owner ∪ assignee ∪ **其项目的成员**」；`GET /requests/{id}` MUST 对
  项目成员开放（此前非相关人员 403）；`mine` 口径不变（仅我创建的）。
- **FR-006**：项目内需求的写操作谓词 MUST 扩展为
  「owner ∪ assignee ∪ 项目成员」：编辑（PATCH，project_id 除外）、
  分配（assign agent/组，009 门禁照查）、依赖边增删（009）。
  **`project_id` 变更仍限创建者或该项目 owner**（R-03）；accept/reject
  仍仅当前 assignee（007 锁定语义，R-07）。
- **FR-007**：建需求时的项目校验 MUST 从「本人名下 active 项目」扩展为
  「active 且（我是 owner 或成员）」；已归档/他人非成员项目维持 400/404。
- **FR-008**：移除成员 MUST NOT 级联改动任何需求（归属人不变、
  分派链不变、Run 不变）——只断开共享关系；被移出者立即失去该项目
  与其需求的可见性（FR-004/005 收紧）。
- **FR-009**：`GET /projects` 的 `request_count` MUST 按调用人可见口径
  计算（成员视角 = 项目内全部，R-06）；`member_count` 为新字段
  （含 owner）。
- **FR-010**：成员管理写操作 MUST 写审计（`project_member_add/remove`，
  detail 带对方 user_id 与项目名）。
- **FR-011**：前端 MUST 在项目管理页提供成员管理（成员列表 + 添加器 +
  移除，owner 可见可操作；成员只读列表）；项目卡片/列表 MUST 区分
  「我的」与「共享的」；被加/被移通知进铃铛。
- **FR-012**：本特性 MUST NOT 改 007 的错误码契约（非相关人员访问既有
  需求端点仍 403——扩展的是「相关人员」集合，不是错误码）；
  MUST NOT 引入部门/树形组织、所有权移交、成员角色第三态
  （用户 2026-11-18 范围确认外）；MUST NOT 改执行引擎、Run 状态机、
  配额口径、009 门禁判据。

### Key Entities

- **项目成员（Project Member）**：`(project_id, user_id)` 一行 = 该用户是
  该项目的成员。可增可删、不更新（改语义就是删旧建新，同 009 边）。
  与 owner 的分工：owner 是 projects 的一列（全权 + 不可变除非删项目），
  成员是单独的表（多人、可变动）。
- **项目（Project）**：不改列；详情响应派生 `member_count`（FR-009）。
- **通知（Notification）**：复用 007 notifications 表，新增
  `project_member_added` / `project_member_removed` 两个类型。
- 不新增：角色表、部门表、成员申请/邀请流（直接加，无审批——
  内部工具；要审批是后续）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**：成员添加后，其 `GET /projects?scope=relevant` 与
  `GET /requests` 在**同一次登录会话内**即可见该项目与需求（真实栈脚本：
  加成员 → 用成员 token 立即查，不等缓存）。
- **SC-002**：成员对项目内需求的完整协作链（建→改→分配→连边）全部
  202/201/200（集成测试逐环断言；断一环共享就名不副实）。
- **SC-003**：非成员对共享项目的访问维持 404/403（收紧后同样立即生效：
  移除成员 → 其 token 再查即 404）。
- **SC-004**：007/008/009 行为零回归——s8（7/7）、s9（5/5）、s10（4/4）、
  s1–s4（29/29）原样重跑全绿；`test_dispatch_visibility` 等既有断言
  （非相关人员 403）一条不改——测试里的陌生人都不是项目成员。
- **SC-005**：成员增删 100% 落审计 + 通知（集成测试断言 audit_logs 与
  notifications 增量；删项目不发通知的纪律也有对应用例）。
- **SC-006**：`GET /projects?scope=relevant` 与 `GET /requests` 的
  RT 不因成员口径退化——P95 <100ms（成员展开是一次额外半连接，
  真实栈脚本计时）。

## Assumptions

- 成员关系是**双向即时**的：owner 加人即生效、踢人即失效，无审批/邀请/
  确认流（内部协作工具；要审批流是后续按需）。
- 「项目内读写」不含 accept/reject（责任接收仍是个人动作，007 锁定）；
  含编辑、分配、依赖边、取消自己的 Run 等既有写操作。
- 停用用户不级联移除成员关系（账号态 ≠ 成员态；需求归属不变）——
  静默撕合同比留一行成员记录更危险。
- owner 不能退出自己创建的项目（409）；所有权移交/项目转让是后续特性
  （现在要退出只能删项目）。
- `member_count` 含 owner 本人（「这个项目有几个人」的自然口径）。
- 成员选择器数据源复用既有 `GET /users/options`（在职用户，member 门）——
  不新造用户搜索端点。
