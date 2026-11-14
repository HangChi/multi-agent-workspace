# Data Model: 核心工作流（Core Workflow）

**Phase 1 产出**：实体、字段、关系、校验不变量、状态迁移。对齐 spec.md 的 Key Entities 与 FR。

## 实体与字段

### users

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | 主键 |
| email | varchar(255) | UNIQUE, NOT NULL | 登录邮箱 |
| password_hash | varchar(255) | NOT NULL | bcrypt/argon2 哈希 |
| role | enum('admin','member','viewer') | NOT NULL, DEFAULT 'member' | RBAC 角色（FR-003） |
| status | enum('active','disabled') | NOT NULL, DEFAULT 'active' | 账号状态 |
| invite_code_id | UUID | FK→invite_codes | 注册所用邀请码 |
| created_at | timestamptz | NOT NULL | |
| updated_at | timestamptz | NOT NULL | |

### invite_codes

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| code | varchar(64) | UNIQUE, NOT NULL | 邀请码 |
| role | enum('member','viewer') | NOT NULL | 该码注册的默认角色 |
| max_uses | int | NOT NULL, DEFAULT 1 | 最大使用次数 |
| used_count | int | NOT NULL, DEFAULT 0 | 已使用次数 |
| expires_at | timestamptz | NULL | 过期时间（NULL=永不过期） |
| created_by | UUID | FK→users | 创建人（admin） |
| created_at | timestamptz | NOT NULL | |

### agents

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| owner_id | UUID | FK→users, NOT NULL | 归属用户（FR-003/FR-004） |
| name | varchar(100) | NOT NULL | |
| description | text | NULL | |
| system_prompt | text | NOT NULL | 角色提示词（FR-005） |
| model | varchar(100) | NOT NULL | 模型标识 |
| temperature | float | NOT NULL, DEFAULT 0.7 | |
| max_steps | int | NOT NULL, DEFAULT 10 | 最大步数 |
| max_run_seconds | int | NOT NULL, DEFAULT 1800 | 执行超时秒 |
| tools | jsonb | NOT NULL, DEFAULT '[]' | 工具集标识列表 |
| cost_limit_cents | int | NOT NULL, DEFAULT 10000 | 成本上限（分） |
| status | enum('active','archived') | NOT NULL, DEFAULT 'active' | FR-004 归档 |
| created_at / updated_at | timestamptz | NOT NULL | |

### agent_groups

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| owner_id | UUID | FK→users, NOT NULL | 归属用户 |
| name | varchar(100) | NOT NULL | |
| description | text | NULL | |
| routing_strategy | enum('direct','fan_out','pipeline','router') | NOT NULL, DEFAULT 'fan_out' | 默认路由策略（FR-007） |
| status | enum('active','archived') | NOT NULL, DEFAULT 'active' | |
| created_at / updated_at | timestamptz | NOT NULL | |

### group_members

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| group_id | UUID | FK→agent_groups, NOT NULL | |
| agent_id | UUID | FK→agents, NOT NULL | |
| position | int | NOT NULL | 组内顺序（FR-007） |
| created_at | timestamptz | NOT NULL | |

**唯一约束**: `(group_id, agent_id)` 唯一；`(group_id, position)` 唯一。

### requests

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| owner_id | UUID | FK→users, NOT NULL | 归属用户 |
| title | varchar(200) | NOT NULL | FR-008 |
| description | text | NULL | |
| expected_output | text | NULL | 期望产出 |
| priority | enum('high','normal','low') | NOT NULL, DEFAULT 'normal' | |
| status | enum('created','assigned','running','completed','failed','cancelled') | NOT NULL, DEFAULT 'created' | 看板状态（FR-009） |
| attachments | jsonb | NOT NULL, DEFAULT '[]' | 附件元数据（key/name/size/mime） |
| created_at / updated_at | timestamptz | NOT NULL | |

### runs

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| request_id | UUID | FK→requests, NOT NULL | 所属需求 |
| parent_run_id | UUID | FK→runs, NULL | 组编排父运行 |
| assignee_type | enum('agent','group') | NOT NULL | 分配目标类型 |
| assignee_id | UUID | NOT NULL | 目标 id |
| strategy | enum('direct','fan_out','pipeline','router') | NOT NULL | 本次路由策略 |
| status | enum('queued','running','needs_input','completed','failed','cancelled','timeout') | NOT NULL, DEFAULT 'queued' | 状态机（FR-015） |
| current_step | int | NOT NULL, DEFAULT 0 | 当前步序号 |
| result | jsonb | NULL | 最终结果 |
| error | text | NULL | 失败原因（可读） |
| cost_cents | int | NOT NULL, DEFAULT 0 | 累计成本（分）（FR-019） |
| started_at / finished_at | timestamptz | NULL | |
| created_at / updated_at | timestamptz | NOT NULL | |

**索引**: `(status, created_at)`、`(request_id)`、`(parent_run_id)`。

### steps

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| run_id | UUID | FK→runs, NOT NULL | |
| seq | int | NOT NULL | 步序号 |
| kind | enum('llm','tool','final') | NOT NULL | 步骤类型 |
| input | jsonb | NULL | 输入（消息/参数） |
| output | jsonb | NULL | 输出（响应/结果） |
| tool_name | varchar(100) | NULL | 若为工具步骤 |
| duration_ms | int | NOT NULL, DEFAULT 0 | |
| cost_cents | int | NOT NULL, DEFAULT 0 | 本步成本 |
| status | enum('running','completed','failed') | NOT NULL, DEFAULT 'running' | |
| created_at / updated_at | timestamptz | NOT NULL | |

**唯一约束**: `(run_id, seq)` 唯一。**索引**: `(run_id)`。

### artifacts

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| run_id | UUID | FK→runs, NOT NULL | |
| step_id | UUID | FK→steps, NULL | |
| object_key | varchar(500) | NOT NULL | MinIO 对象键 |
| filename | varchar(255) | NOT NULL | 原始文件名 |
| size_bytes | bigint | NOT NULL | |
| mime_type | varchar(100) | NULL | |
| created_at | timestamptz | NOT NULL | |

### audit_logs

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| user_id | UUID | FK→users, NOT NULL | |
| action | varchar(50) | NOT NULL | login/agent_create/assign/cancel/... |
| target_type | varchar(50) | NULL | agent/group/request/run |
| target_id | UUID | NULL | |
| detail | jsonb | NULL | 附加信息 |
| created_at | timestamptz | NOT NULL | |

**索引**: `(user_id, created_at)`、`(action)`。

## 校验不变量

1. 用户邮箱全局唯一；密码哈希存储，禁止明文。
2. Agent 的 `owner_id` 与所有操作者一致才能读写（资源归属，FR-003）。
3. Agent 组删除时若仍有成员，须级联清空 group_members（禁止悬空引用）。
4. 删除被组引用的 Agent 被拒绝（Edge Cases「删除被引用的 Agent」），仅允许归档。
5. 需求 `status='running'` 时禁止再次分配（FR-012，由状态机保证）。
6. 邀请码：`used_count < max_uses` 且未过期才可用（Edge Cases 无效/过期码）。
7. Run 的 `cost_cents` 单调不减，且 ≤ 对应 Agent 的 `cost_limit_cents`（FR-019）。
8. Run 状态迁移必须合法（见下节状态迁移表），非法迁移被拒绝。
9. `steps.seq` 从 1 开始连续递增（`(run_id, seq)` 唯一）。

## 状态迁移

### requests.status

```
created ──(分配)──► assigned ──(入队成功)──► running
running ──(执行完成)──► completed
running ──(执行失败)──► failed
running ──(用户取消)──► cancelled
```

### runs.status

```
queued ──(Worker 开始)──► running
running ──(需要补充)──► needs_input ──(用户回复)──► running
running ──(成功)──► completed
running ──(业务失败)──► failed ──(手动重试)──► queued
running ──(超时)──► timeout
queued/running ──(用户取消)──► cancelled
```

**非法迁移**: completed/failed/timeout/cancelled 为终态，不可再迁移；failed 仅可迁移到 queued（重试，需重置 current_step/cost 并清 error）。

## 源→产物字段映射

| 源（spec FR） | 产物（表字段） | 说明 |
|---|---|---|
| FR-008 需求字段 | requests.title/description/expected_output/priority | 创建需求 |
| FR-005 Agent 配置 | agents.system_prompt/model/temperature/max_steps/cost_limit_cents | Agent 配置 |
| FR-007 组配置 | agent_groups.routing_strategy + group_members.position | 组与成员 |
| FR-010 分配 | runs.assignee_type/assignee_id/strategy | 拖拽分配 |
| FR-013 入队 | runs.status='queued' | 入队 |
| FR-014 执行 | steps（kind=input/tool/final） | 分步记录 |
| FR-016 制品 | artifacts（run_id/object_key/...） | 产出 |
| FR-019 成本 | runs.cost_cents + steps.cost_cents | 实时成本 |

## 偏差清单

- **D-1**: spec 未定义「附件」的数据模型细节，本模型以 jsonb 元数据数组 + MinIO 对象存储落地（attachment 元数据存 requests.attachments，文件体在 MinIO）。对齐 R-05。
- **D-2**: spec 的 runs 状态含「timeout」，而 requests 状态不含（需求层面超时仍归为 failed）。本模型保持两者独立，runs.timeout 与 requests.failed 对应。
- **D-3**: spec 未提 invite_codes 实体，本模型为「邀请码注册」（FR-001 + Assumptions）新增该实体与关联。
