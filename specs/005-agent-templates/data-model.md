# Data Model: Agent 模板与一键克隆

**Branch**: `feat/005-agent-templates` | **Date**: 2026-11-18 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

## Design rules

1. **模板是快照，Agent 是实体**。模板表的 10 个配置字段与 `agents` 的同名字段**逐字同义**，
   但模板没有 `status`、没有运行、没有组引用——它是「一段待实例化的配置」。
2. **配置字段的单一事实源在 schema 层**。`AgentCreate` 与 `AgentTemplateCreate` 共享
   同一组字段定义（见 contracts/api.md §5 的字段表），不允许两边各写一份默认值。
3. **来源外键一律 SET NULL**。删模板/删源 Agent 不带走产物（FR-021）。
4. **内置模板用 `owner_id IS NULL` 表达**，不引入 `is_builtin` 布尔列——
   少一个可能互相矛盾的状态源（「owner 为空但不是内置」这种行不该存在）。

## Entities

### AgentTemplate（新表 `agent_templates`）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | uuid | PK | `new_uuid()` |
| `owner_id` | uuid | FK → `users.id` ON DELETE CASCADE，**可空** | NULL = 内置模板；非 NULL = 成员私有模板 |
| `name` | varchar(100) | NOT NULL，CHECK `name <> ''` | 同一所有者下唯一（见下方唯一性说明） |
| `description` | text | 可空 | 一句话说明适用场景 |
| `system_prompt` | text | NOT NULL，CHECK `system_prompt <> ''` | 与 `agents.system_prompt` 同义 |
| `model` | varchar(100) | NOT NULL | 内置模板由种子脚本取 `LLM_MODEL`；成员模板快照自 Agent |
| `temperature` | float | NOT NULL，CHECK `0.0 <= temperature <= 2.0` | 与 `agents.temperature` 同域 |
| `max_steps` | int | NOT NULL，CHECK `1 <= max_steps <= 100` | 同上 |
| `max_run_seconds` | int | NOT NULL，CHECK `1 <= max_run_seconds <= 86400` | 同上 |
| `tools` | jsonb | NOT NULL，默认 `[]` | 工具名数组；创建 Agent 时按目录校验与过滤（FR-008） |
| `cost_limit_cents` | int | NOT NULL，CHECK `cost_limit_cents >= 1` | 同上 |
| `egress_mode` | enum(`agent_egress_mode`) | NOT NULL，默认 `default` | **复用 002 已建的同名 enum**，不新建类型 |
| `created_at` | timestamptz | NOT NULL，默认 now | |
| `updated_at` | timestamptz | NOT NULL，默认 now，onupdate now | |

**唯一性（关键，见 research R-05）**：

```sql
-- 成员私有模板：同一所有者下名称唯一
CONSTRAINT uq_agent_templates_owner_name UNIQUE (owner_id, name)
-- 内置模板：PostgreSQL 唯一约束遇 NULL 不生效，必须用部分唯一索引
CREATE UNIQUE INDEX uq_agent_templates_builtin_name
    ON agent_templates (name) WHERE owner_id IS NULL;
```

`UNIQUE(owner_id, name)` 对 `owner_id IS NULL` 的若干行**完全不设防**——
不建上面的部分索引，种子脚本重复执行就会插入重名内置模板。

**索引**：

- `idx_agent_templates_owner (owner_id, created_at)`：成员列表主路径（倒序取自己的）。
  复合索引已覆盖 `owner_id` 单列查询。
- `uq_agent_templates_builtin_name`：内置唯一性 + 「按名找内置模板」的种子幂等路径。

**CHECK 约束**（与 `agents` 的既有不变量对齐，避免「模板能存、Agent 不能建」的错配，
即 FR-022）：

- `ck_agent_templates_name_not_blank`：`name <> ''`
- `ck_agent_templates_prompt_not_blank`：`system_prompt <> ''`
- `ck_agent_templates_temperature_range`：`temperature BETWEEN 0.0 AND 2.0`
- `ck_agent_templates_max_steps_range`：`max_steps BETWEEN 1 AND 100`
- `ck_agent_templates_max_run_seconds_range`：`max_run_seconds BETWEEN 1 AND 86400`
- `ck_agent_templates_cost_limit_positive`：`cost_limit_cents >= 1`

### Agent（既有表 `agents`，新增两个可空列）

| 新列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `source_template_id` | uuid | FK → `agent_templates.id` ON DELETE SET NULL，可空，索引 | 该 Agent 由哪个模板创建（US1） |
| `source_agent_id` | uuid | FK → `agents.id` ON DELETE SET NULL，可空，索引 | 该 Agent 克隆自哪个 Agent（US2） |

- 两者**可同时为 NULL**（手工创建），也**不应同时非 NULL**（一次创建只有一种来源）；
  该不变量由服务层保证，不建 CHECK（跨行语义，DB 层表达代价高于收益，且写错时
  应用层直接拒绝）。
- `source_agent_id` 指向的源 Agent 被删除时，外键 SET NULL；克隆体自身不受影响。
- **自引用外键**：`agents.source_agent_id → agents.id` 是自引用，删除一行时
  PostgreSQL 会在同表内级联求值；`ON DELETE SET NULL` 语义明确，无递归删除风险
  （SET NULL 不触发对被引用行的删除动作）。

### 不新建的实体

- **不建**「模板使用记录」表：spec 明确排除使用统计（Scope Boundaries）。
- **不建**「模板版本」表：模板只有当前快照（spec Assumptions）。
- **不改** `requests` / `runs` / `steps` / `artifacts`：克隆产物是普通 Agent，
  其运行历史从零开始，不需要任何来源标记。

## Enums

| Enum | 新建？ | 说明 |
|---|---|---|
| `agent_egress_mode`（`default` / `restricted`） | **复用 002 已建** | 迁移里用 `create_type=False` 引用，与 0007 引用 `request_priority` 同一手法 |

本特性**不新建任何 enum 类型**。

## State transitions

模板没有状态机（无 `status` 列）：存在即可用，删除即消失。
Agent 的状态机完全不变（`active` / `archived`），克隆与模板创建的产物一律 `active`
（FR-014：归档态源 Agent 的克隆产物仍是活跃态）。

```
AgentTemplate:  (不存在) --另存为模板/种子注入--> (存在) --PATCH--> (存在) --DELETE--> (不存在)
                        ↑ 删除不影响任何已创建的 Agent（FR-021）

Agent:          (不存在) --POST /agents/from-template/{id}--> active
                (不存在) --POST /agents/{id}/clone---------> active
```

## 删除语义（易错，单独说明）

| 操作 | 连带效果 | 依据 |
|---|---|---|
| 删除模板 | 已创建 Agent **全部保留**，其 `source_template_id` 置 NULL | FR-021 |
| 删除 Agent（源） | 克隆体**全部保留**，其 `source_agent_id` 置 NULL | FR-021 |
| 删除用户 | 其私有模板级联删除；内置模板无 owner 不受影响；其 Agent 按既有规则处理 | `owner_id` FK CASCADE |
| 删除内置模板 | **不允许**（FR-015 只读），服务层拒绝，DB 层不设特殊通道 | FR-015 |

## 迁移计划

**`0009_agent_templates.py`**（down_revision = `0007_schedules`）：

1. `op.create_table("agent_templates", ...)`：含上述全部列、6 个 CHECK、
   `UNIQUE(owner_id, name)`；`egress_mode` 用 `create_type=False` 引用既有 enum。
2. `op.create_index("idx_agent_templates_owner", ...)`。
3. `op.create_index("uq_agent_templates_builtin_name", ..., unique=True, postgresql_where=sa.text("owner_id IS NULL"))`。
4. `op.add_column("agents", "source_template_id")` + FK `agents_source_template_id_fkey`
   （SET NULL）+ 索引 `ix_agents_source_template_id`。
5. `op.add_column("agents", "source_agent_id")` + FK `agents_source_agent_id_fkey`
   （SET NULL）+ 索引 `ix_agents_source_agent_id`。
   **自引用外键**：`op.create_foreign_key` 指向同表 `agents.id`，无需额外处理。
6. downgrade 逆序：先删 `agents` 的两个 FK/索引/列，再删 `agent_templates` 表与索引；
   不 drop 任何 enum（复用的 002 类型归 002 管）。

**不写进迁移**：内置模板数据（research R-02）——由 `scripts/seed_agent_templates.py` 注入。

## 不做哪些变更（明确声明）

- **不改** `agents` 既有任何列的语义、默认值或约束（只加两个可空列）。
- **不建** `is_builtin` 布尔列（用 `owner_id IS NULL` 表达，避免第二状态源）。
- **不建** 模板版本表、使用记录表、软删列。
- **不给** `agent_templates.tools` 建 GIN 索引（本量级顺序扫描足够，且工具集是整体读取）。
- **不引入** croniter 之外的任何新依赖（本特性零新依赖）。
