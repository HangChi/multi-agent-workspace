# Feature Specification: 项目所有权移交（017）

**Feature Branch**: `feat/017-project-ownership-transfer`

**Created**: 2026-11-21

**Status**: Draft

**Input**: 用户 2026-11-21「按 README 里的顺序逐个优化/实现」。010 把
「owner 不能退出自己创建的项目（409）」写成了已知限制并注明「所有权移交是后续特性」，
`project_member_service.remove_member` 里也留着同一句注释（R-04）。本特性把这条挂账结清。

## 问题陈述

项目是单人归属（`projects.owner_id`），010 加了成员表但**没有移交路径**，于是：

1. **离职 / 转岗场景只有一条路：删项目。** 而删项目是物理删除 + 需求归属清空
   （008 R-02）——「我走了，这些活的历史归属也一起没了」。
2. **409 的文案把用户往错的方向赶。** 现在原话是
   「创建者不能退出自己创建的项目（可删除项目）」，
   但它真正该说的是「请先把项目移交给别人，或删除项目」。
3. **协作者无法接手。** 成员能读写项目内的需求，却改名、管成员、归档、删除
   一样都做不了；想「让老张接手这个项目」在系统里无法表达。

这不是权限模型的缺口，是**归属权没有出口**的缺口。

## User Scenarios & Testing *(mandatory)*

### US1 — 创建者把项目移交给成员，自己保留访问（优先级 P1）

作为项目创建者，我要把项目移交给某个成员，并且**继续能进这个项目**——
我是原经手人，交接后还要看历史。

**验收**（Given/When/Then）：
- Given 项目 P 的 owner 是 A，B 是 P 的成员；
- When A 调 `POST /projects/P/transfer {new_owner_id: B, keep_as_member: true}`；
- Then P 的 `owner_id` 变成 B；A 出现在成员表里；B 从成员表移除（owner 不在成员表，
  010 R-01 的不双写纪律）；A 仍能读 P 与其需求。

### US2 — 移交后完全退出（优先级 P1）

作为要离职的创建者，我要把项目交出去后**彻底从项目里消失**，
不要留一个「已离职但仍挂名」的成员行。

- Given 同 US1，`keep_as_member: false`；
- Then A 既不是 owner 也不是成员；A 访问 P 得 **404**（不是 403——016 的不暴露
  存在性纪律）；A 的项目列表里看不到 P。

### US3 — 新 owner 拿到完整权限（优先级 P1）

作为接手人，移交完成后我要能做 owner 能做的一切，**不需要别人再操作一次**。

- Given 移交已在 US1 完成；
- Then B 能改名 P、加/踢成员、归档 P、删 P（各端点逐一验，不是只看 ProjectOut）。

### US4 — 同名冲突说得清是哪一条（优先级 P2）

作为创建者，如果接手人已经有同名项目，我要知道**冲突的原因**，
而不是收到一句「约束冲突」。

- Given B 已有一个名为「官网改版」的项目，A 的项目也叫「官网改版」；
- When A 移交 A 的项目给 B；
- Then **409**，message 明确指向「对方已有同名项目」并带上项目名；
  项目归属**不变**（事务整体回滚，不能出现「A 丢了、B 没拿到」）。

### US5 — 新 owner 收到通知（优先级 P2）

作为接手人，我要在站内收到「你成为了 X 项目的创建者」，
否则我要等下次打开项目列表才发现责任变了。

- Given 移交成功；
- Then B 的收件箱有一条 `project_ownership_received`，标题是项目名。

### 边界与异常

| 场景 | 行为 |
|---|---|
| 非 owner（成员）发起移交 | **403**（他知道项目存在，不够权——010 同款分工） |
| 陌生人发起移交 | **404**（不暴露存在性） |
| `new_owner_id` 等于自己 | **409**「不能移交给自己」 |
| `new_owner_id` 不是该项目成员 | **409**「目标用户不是项目成员，请先加为成员」 |
| 目标用户的角色是 viewer | **409**「只读角色不能接收项目」 |
| 目标用户不存在 | **404**「用户不存在」（显式查人，与「项目不存在」共用同一文案家族，
但因为是**显式指名的用户 id**，此处允许 404 区分——见 research R-03） |
| 目标用户已停用 | **400**「用户已停用，不能接收项目」（010 `add_member` 的同款判断） |
| 项目已归档 | 允许移交（归档只拦新建需求，不拦归属变更） |
| 两个 owner 同时移交同一项目 | 行锁串行化，第二个请求看到 owner 已变 → 403 |

## Requirements *(mandatory)*

### Functional

- **FR-001** 系统 MUST 提供「移交项目所有权」的单一端点，入参为目标用户与
  「是否保留为成员」，仅当前 owner 可调用。
- **FR-002** 移交 MUST 在**一个事务**内完成：`owner_id` 更新、成员行增删、
  通知落库、审计落库。任一失败整体回滚。
- **FR-003** 移交后 `owner_id` 指向新 owner；成员表 MUST NOT 出现新 owner 的行
  （010 R-01：owner 不双写）；原 owner 按 `keep_as_member` 决定是否写成员行。
- **FR-004** 系统 MUST 在移交前校验目标用户的项目名唯一性
  （`uq_projects_owner_name`），冲突时 409 且 message 可解释，**不得**依赖
  数据库异常兜底（那会变成 500 或不可读的约束名）。
- **FR-005** 系统 MUST 校验目标用户是项目成员、在职、且角色为 member/admin；
  校验顺序固定：项目归属（404/403）→ 目标存在性 → 目标账号状态 → 目标角色 →
  目标成员关系 → 同名冲突 → 落库。
- **FR-006** 落库前 MUST 对项目行加锁（`SELECT ... FOR UPDATE`），
  防止「移交」与「改名/删除/踢人」并发交错。
- **FR-007** 成功时 MUST 给新 owner 发一条站内通知（`project_ownership_received`）。
- **FR-008** 成功时 MUST 写审计 `project_transfer`，detail 含 `from_user_id`、
  `to_user_id`、`keep_as_member`、`project_name`。
- **FR-009** `remove_member` 中 owner 自移除的 409 文案 MUST 改为
  「创建者不能退出自己创建的项目：请先移交所有权，或删除项目」。
- **FR-010** 前端项目卡片 MUST 为 owner 提供「移交」入口：选择成员 + 是否保留为成员，
  成功后列表刷新（该项目可能从「我的」变成「共享的」）。

### Non-functional

- **NFR-001** 无数据库迁移：复用既有 `projects.owner_id` 与 `project_members`。
- **NFR-002** 端点遵守 016 的错误码矩阵：非相关人 404、参与者缺权限 403、
  角色不足 403；409 只用于**状态冲突**（同名、目标非法、自移交）。
- **NFR-003** 后端单测覆盖六个 409/404/403 分支与事务回滚；
  前端至少一条组件测试；e2e 一条真实浏览器用例（B4 套件）。

## Key Entities

| 实体 | 变化 |
|---|---|
| `projects.owner_id` | 值变更（无 schema 变更） |
| `project_members` | 新增原 owner 行（keep_as_member）／删除新 owner 行 |
| `notifications` | 新增 `project_ownership_received` |
| `audit_logs` | 新增 `project_transfer` |

## Success Criteria *(mandatory)*

- **SC-001** 创建者能完成移交并在 `GET /projects?scope=mine|relevant` 里看到预期变化
  （自己：mine 少一条、relevant 视 keep_as_member 而定；接手人：mine 多一条）。
- **SC-002** 移交后新 owner 的四个 owner 动作全部可用（改名 / 加成员 / 归档 / 删除），
  原 owner 的四个动作全部 403（keep_as_member=true 时）或 404（false 时）。
- **SC-003** 同名冲突时归属不变，且错误消息能让人知道是「对方已有同名项目」。
- **SC-004** 所有既有测试（后端 1462+ / 前端 143）保持绿，`remove_member` 的
  文案变更不破坏既有用例。

## Out of Scope

- 多 owner / 共同所有权（014 之前就确认的单人归属，本轮不动）；
- 移交审批流（需要接手人确认）、批量移交、移交历史表；
- 部门 / 树形组织、所有权转让的定时生效；
- 「原 owner 保留为成员」时的角色区分（010 明确无 role 列）。
