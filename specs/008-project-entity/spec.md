# Feature Specification: 项目实体与需求归属（008）

**Feature Branch**: `feat/008-project-entity`

**Created**: 2026-11-18

**Status**: Draft

**Input**: 用户 2026-11-18 确认的路线图：「先做 P0-1~3（数据模型 + 分派 API + 状态机）……
然后 P1 项目容器；P2 按实际组织形态按需添加」。007（P0）已合并 master，本特性即 P1。
范围经用户二次确认（2026-11-18）：**项目实体 CRUD + 需求归属 + 看板按项目筛选**；
需求间依赖边（DAG 编排）明确不在本轮，留作后续特性。

## User Scenarios & Testing *(mandatory)*

### US1 — 创建项目并给需求归类（优先级 P1）

作为需求创建者，我希望先建几个项目（如「官网改版」「Q4 增长」），建需求时直接选项目，
让看板上散落的一堆卡片有个归属。

**Why this priority**：这是整个特性的地基——没有项目实体，归属与筛选都无从谈起；
且单独实现它就有价值（能建项目、能归类、能按项目看）。

**Independent Test**：登录 member → 建 2 个项目 → 建 3 条需求分别归属 →
看板按项目筛选只看到对应需求。全程不需要 US2/US3。

**Acceptance Scenarios**：

1. **Given** member 已登录且名下无项目，**When** `POST /projects`（name=官网改版，
   description=可选），**Then** 201 返回 `{id,name,description,status:"active",...}`，
   该项目出现在 `GET /projects` 列表首位（按创建时间倒序）。
2. **Given** 已存在项目 A，**When** 建需求时传 `project_id=A.id`，
   **Then** 需求详情与列表项的 `project_id` 均为 A.id，`project_name` 为「官网改版」。
3. **Given** 已存在项目 A 与项目 B、A 下有 2 条需求、B 下有 1 条，
   **When** `GET /requests?project_id=A.id`，**Then** 只返回 A 下的 2 条
   （与 `scope` 参数正交：仍受「我创建的 + 分派给我的」约束）。
4. **Given** 同名项目「官网改版」已存在，**When** 再次 `POST /projects` 同名，
   **Then** 409 CONFLICT（同一用户名下项目名唯一，与 agent_templates 同纪律）。

### US2 — 项目管理：改名、归档、删除（优先级 P2）

作为项目创建者，我希望项目能改名（打错字）、能归档（做完了但记录要留）、
能删除（建错了）。归档项目里的历史需求不能被悄悄弄丢。

**Why this priority**：没有管理动作，项目建错就无法纠正，实体就是一次性垃圾。

**Independent Test**：建项目 → 改名成功 → 归档后该项目不再出现在「可选项目」列表 →
删除后需求 `project_id` 变 null 但需求本身与 Run 全部保留。

**Acceptance Scenarios**：

1. **Given** 项目 A 存在，**When** `PATCH /projects/{id}`（name=新名），
   **Then** 200 且 `GET /projects` 显示新名；关联需求的 `project_name` 同步变。
2. **Given** 项目 A 下有 2 条进行中的需求，**When** `PATCH /projects/{id}`
   （status=archived），**Then** 200；这 2 条需求的 `project_id` **不变**、
   详情仍显示归属；但建新需求时不能再选 A（400 INVALID_INPUT）。
3. **Given** 项目 A 下有 3 条需求，**When** `DELETE /projects/{id}`，
   **Then** 200；3 条需求的 `project_id` 全部变 null（外键 SET NULL），
   需求本身、其 Run、经手链、通知全部保留，看板上不消失。
4. **Given** 他人名下项目 B，**When** 我 PATCH/DELETE B，**Then** 404
   （不暴露存在性，同 006 锁定契约）。

### US3 — 看板按项目筛选（优先级 P2）

作为每天看板上扫需求的人，我希望在工具条用下拉框按项目过滤，
在卡片上直接看到「这件活属于哪个项目」。

**Why this priority**：筛选是「归属」的价值兑现——归类了却看不出来等于没归类。

**Independent Test**：member 登录看板 → 工具条项目下拉选「官网改版」→
列表只剩该项目需求；卡片上显示项目名徽章；切回「全部」恢复。

**Acceptance Scenarios**：

1. **Given** 看板已加载且用户名下有 ≥1 个项目，**When** 打开工具条项目下拉，
   **Then** 选项为「全部项目」+ 我的各项目（含每条项目下的需求计数）。
2. **Given** 已选项目 A 筛选，**When** 看板刷新（轮询/窗口聚焦），
   **Then** 筛选态保持，URL 可分享（`?project=<id>` 深链，同既有 `?request=` 约定）。
3. **Given** 某需求无归属（project_id=null），**When** 选中具体项目筛选，
   **Then** 该需求被过滤掉；选「全部项目」时回来。
4. **Given** viewer 打开看板，**When** 查看项目下拉，**Then** 无下拉
   （viewer 对 /projects 与 /requests 列表都是 403，前端不给入口）。

### Edge Cases

- 建需求时传的 `project_id` 不存在、属他人、或已归档 → 分别 404 / 404 / 400
  （归档项目是「看得见但不能用」，与已删除区分）。
- `PATCH /requests/{id}` 改 `project_id`：**仅需求创建者可改**——经手人把需求
  挪到自己名下项目，会让创建者的项目视图静默丢失这件活（007 分派链路的教训：
  可见性变更必须可解释）。
- 项目名边界：空/纯空白 → 422；>100 字符 → 422（与 Agent/组名同长度纪律）。
- 删除项目与并发建需求（该需求正好选中被删项目）→ 行级锁下后者 404，不出现悬挂引用。
- 归档项目的删除仍允许（归档只是「停止接收新需求」，不是保护罩）。
- 已归档项目里的需求仍可正常分派/接收/执行/退回——归档只影响「新建时不可选」。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**：系统 MUST 提供项目实体 CRUD（`POST/GET/GET-one/PATCH/DELETE /projects`），
  数据模型为 `projects` 表：`id / owner_id / name(≤100) / description(可空) /
  status(active|archived) / created_at / updated_at`，`UNIQUE(owner_id, name)`。
- **FR-002**：项目 MUST 归属创建者；非创建者的一切操作（读详情/改/删）MUST 得到 404
  （延续 agents/groups/requests 的归属纪律，不暴露存在性）。
- **FR-003**：viewer MUST 对所有 /projects 端点与 /requests 列表得到 403
  （与 agents/groups 的 RBAC 完全一致，member/admin 可用）。
- **FR-004**：需求 MUST 获得可空 `project_id` 外键（`ON DELETE SET NULL`）与
  `project_name` 展示字段；创建/编辑时可设置，取值必须是「本人名下 active 项目」。
- **FR-005**：`project_id` 的变更 MUST 仅限需求创建者；经手人（当前接收人）
  改其他字段维持 007 的 owner-or-assignee 谓词，改 `project_id` 一律 403。
- **FR-006**：`GET /requests` MUST 支持 `?project_id=<uuid>` 过滤，与
  `scope / status / q / priority / page` 正交叠加；`project_id=null` 语义
  （未归属）通过 `?project=none` 表达（URL 里塞 "null" 字符串是自找的歧义）。
- **FR-007**：项目归档（status=archived）MUST 不级联改动任何需求；已归档项目
  MUST 不能被新建需求选中（400），但既有归属、分派、执行链路全部照常。
- **FR-008**：删除项目 MUST 把其下需求 `project_id` 置空（SET NULL），
  需求/Run/经手链/通知/附件一律不删、不归档、不变状态。
- **FR-009**：项目 CRUD 的写操作 MUST 写审计（action=`project_create/update/
  delete`）。
- **FR-010**：`GET /projects` MUST 返回每个项目的需求计数（按当前用户可见范围：
  我创建的 + 分派给我的），供筛选下拉直接展示，避免前端 N+1 请求。
- **FR-011**：本特性 MUST NOT 引入需求间依赖边/DAG/拓扑排序（用户 2026-11-18
  明确留后续）；MUST NOT 改执行引擎、状态机、配额、Run 归属。
- **FR-012**：前端 MUST 提供项目管理页（列表/新建/改名/归档/删除）+ 建需求表单的
  项目选择器 + 看板工具条项目筛选下拉 + 卡片项目徽章 + `?project=` 深链。

### Key Entities

- **项目（Project）**：需求的容器。`owner_id`（创建者，唯一归属人）、
  `name`（同用户名下唯一）、`description`、`status`（active/archived）。
  与 Agent/组的关键差异：Agent/组是「执行资源」，项目是「需求的业务归属」，
  没有成员、没有策略、不参与执行。
- **需求（Request）**：新增 `project_id`（可空外键，SET NULL）与 `project_name`
  （展示快照，随项目改名同步——与 004 `source_schedule_name` 同款「可空展示字段」模式）。
- 不新增：依赖表、项目成员表、项目统计表（FR-010 的计数由实时聚合出，不物化）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**：member 从「建项目」到「建一条归属该项目的需求并在看板按项目筛出它」
  全流程 ≤4 次点击/请求，单次 API 受理 P95 <100ms（与既有 /requests 同量级，
  实测手段同 s8：真实栈脚本计时）。
- **SC-002**：删除一个含 20 条需求的项目后，20 条需求全部保留且 `project_id` 为 null，
  0 条需求/Run/通知丢失（集成测试用真实 DB 断言三张表计数）。
- **SC-003**：归档项目下的进行中需求，其分派/接收/退回/执行链路行为与无归属需求
  完全一致（回归 s8 脚本在「需求有归属」的数据上重跑仍 7/7）。
- **SC-004**：看板在 200 条需求、20 个项目的数据量下，项目筛选下拉与列表
  过滤不产生 N+1 请求（前端网络面板或测试内请求计数 ≤3）。
- **SC-005**：项目 CRUD 与归属变更 100% 落审计（集成测试断言 audit_logs 增量）。
- **SC-006**：既有 007 行为零回归——`/requests` 不传 `project_id` 时的响应
  与 007 逐字段一致（新字段可空且有默认，既有集成测试全绿不改一条）。

## Assumptions

- 项目为**单人归属**（owner_id），不做成员/协作/共享——P2「按实际组织形态」
  的口径是把组织形态后置，本轮用「每人管自己的项目」验证容器价值。
  依据：实测当前 5 个用户各 2–6 条需求，无跨用户归因先例。
- 已归档项目对**历史可见**：详情/列表/筛选都还能按归档项目过滤
  （`?project_id=` 允许传归档项目 id）——「归档」只拦新建，不拦回看。
- 删除项目是软风险操作：SET NULL 而非 RESTRICT/CASCADE。选择依据与 004
  `source_schedule_id` 一致——「规则删了，它建出的需求还在」。
- `project_name` 为物化展示字段而非 JOIN：看板列表一次查 N 条需求，
  JOIN projects 会让过滤+分页+计数的 SQL 复杂度和锁面都上升；改名同步由
  PATCH 端点一行 UPDATE 完成，与 004 schedule_name 同模式。
- 不引入项目级配额/成本汇总（执行成本恒记 request.owner_id 的 007 纪律不动）；
  若将来要「项目花了多少钱」，走 stats 特性按 project_id 聚合 runs 即可。
