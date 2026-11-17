# API Contracts: Agent 模板与一键克隆

**Branch**: `feat/005-agent-templates` | **Date**: 2026-11-18 | **Spec**: [spec.md](spec.md)

所有接口前缀 `/api/v1`，除注册/登录外均需 `Authorization: Bearer <access_token>`。
错误体统一为 `{"error": {"code": "...", "message": "..."}}}`（既有约定，见 `app/api/errors.py`）。

**权限基线**：模板与克隆全部要求 `member`/`admin`（viewer 403），与 `POST /agents` 一致。
`viewer` 连模板列表都看不到（模板含系统提示词，属配置资产）。

---

## 1. 模板管理 `/api/v1/agent-templates`

### 1.1 `GET /agent-templates`

模板列表：**内置模板（全部）+ 自己的模板**。默认同屏返回，前端分区展示。

| Query | 类型 | 默认 | 说明 |
|---|---|---|---|
| `scope` | `all` \| `mine` \| `builtin` | `all` | 列表范围；`mine` 不含内置 |
| `q` | string(≤100) | — | 名称/描述模糊匹配（与 `GET /agents` 同语义） |
| `page` / `page_size` | int ≥1 / 1..100 | 1 / 20 | 分页；`total` 为过滤后全量条数 |

**200 响应**：

```json
{
  "items": [
    {
      "id": "…",
      "owner_id": null,
      "is_builtin": true,
      "name": "通用问答助手",
      "description": "…",
      "system_prompt": "…",
      "model": "Deepseek-V4.1-Flash",
      "temperature": 0.7,
      "max_steps": 10,
      "max_run_seconds": 300,
      "tools": ["web_search", "read_url"],
      "cost_limit_cents": 500,
      "egress_mode": "default",
      "created_at": "…",
      "updated_at": "…"
    }
  ],
  "total": 7
}
```

`is_builtin` 是**派生字段**（`owner_id is null`），不落库——避免出现「第二状态源」
与 `owner_id` 互相矛盾（data-model.md Design rules #4）。

### 1.2 `GET /agent-templates/{template_id}`

模板详情。**他人模板与不存在返回同一措辞**（FR-004）：`404 NOT_FOUND / "模板不存在"`。
**MUST NOT** 用「无权访问」区分两者——那会泄露资源存在性（与 004 FR-006 同一纪律）。

### 1.3 `PATCH /agent-templates/{template_id}`

编辑**自己的**模板（部分字段，字段表见 §5）。

- `200` → `AgentTemplateOut`
- `404 NOT_FOUND`：不存在或不属于自己（同一措辞）
- `403 FORBIDDEN / "内置模板不可编辑"`：`owner_id is null`
- `409 CONFLICT / "同名模板已存在"`：撞名（FR-003）
- `422 VALIDATION_ERROR`：字段校验失败（与 `PATCH /agents` 同一套 schema 语义）

### 1.4 `DELETE /agent-templates/{template_id}`

删除自己的模板。已由它创建的 Agent **全部保留**，其 `source_template_id` 置 NULL（FR-021）。

- `204` 无响应体
- `404 NOT_FOUND`：不存在或不属于自己
- `403 FORBIDDEN / "内置模板不可删除"`

### 1.5 `POST /agents/{agent_id}/save-as-template`（US3 沉淀入口）

把自己的一个 Agent 的当前配置快照为模板。请求体只给模板的**元信息**，配置全部来自 Agent：

```json
{ "name": "代码审查助手", "description": "可选，一句话说明适用场景" }
```

- `201` → `AgentTemplateOut`（`is_builtin: false`）
- `409 CONFLICT / "同名模板已存在"`：撞名时拒绝并让用户改名，**不自动加后缀**
  （避免模板库出现「XX 副本」噪音，spec Assumptions）
- `403 FORBIDDEN / "无权操作他人 Agent"`：Agent 属于他人（viewer 同样 403）
- 源 Agent 处于 `archived` **允许**保存（US3 AS5）

> **状态码不对称是刻意记录在案的**：模板用 404 同措辞（FR-004，防存在性泄露），
> Agent 用 403 —— 因为 `PATCH/DELETE /agents/{id}` 一向是 403「无权操作他人 Agent」，
> 改它属于 001 契约变更，不在本特性范围。新端点跟随所在资源的既有约定，
> 不为了一致性而制造第二套 Agent 语义。

---

## 2. 从模板创建 `POST /api/v1/agents/from-template/{template_id}`（US1）

请求体 = `AgentFromTemplateIn`：与 `AgentCreate` **逐字段同构，只有 `tools` 不同**
（见下方「为什么 tools 从宽」）。前端「从模板创建」打开时表单被模板值预填，
用户可以直接提交（一键确认），也可以改任意字段后再提交——两条路提交的是同一种
对象，服务端以「模板值打底 → 请求体覆盖」求值。

> **为什么不支持「空 body 即用模板值」**：那需要一个全可选字段的 schema，
> 还要给「必填字段缺失」单开一条回落分支。而 spec 的一键确认本来就走表单
> （FR-005/006：「表单被预填…也可以直接一键确认」），前端手里已经有模板值，
> 原样提交即可。

### 为什么 `tools` 从宽（本端点唯一的刻意偏差）

`AgentCreate.tools` 挂著工具目录白名单校验器，不在目录里的名字直接 422。
但模板完全可能引用一个**创建后被下线**的工具（002 FR-005 就有下线机制）——
用户只是原样提交了预填好的表单，却会撞上一个他没打过的 422，
FR-008 要求的「创建成功 + 过滤 + 提示」永远走不到。

因此本端点的 `tools` **不做目录校验**，改由服务层过滤并进 `warning`；
**其余字段与 `AgentCreate` 完全一致**（`tests/unit/test_agent_template_schema.py`
逐字段钉住）。这不是第二条宽松旁路——FR-008 本身就规定了该字段从宽，
FR-009 的「同一套校验」指的是其余字段。

- `201` → `AgentSaveOut`（既有响应体，含 `warning`）
- `404 NOT_FOUND / "模板不存在"`：模板不存在、不属于自己且非内置（同一措辞）
**校验口径（与 `POST /agents` 逐字一致，两条失败路径都要有）**：

- 工具名**不在工具目录**（`CATALOG`）→ **422 VALIDATION_ERROR**（Pydantic 字段校验器，
  与 `AgentCreate` 同一机制）。
- 工具在目录中但**未启用/已下线** → **400 INVALID_INPUT**
  （`validate_grants`，message 形如 `工具未启用或已下线: xxx；可选工具: …`）。
  模板实例化路径**不走**这条：失效工具被过滤并进 `warning`（FR-008）。

**工具过滤（FR-008）**：模板的 `tools` 中已下线/未启用的项被静默过滤，
被过滤的工具名进入 `warning`：

```json
{ "warning": "模板中的工具已下线，未启用：legacy_shell" }
```

多个被过滤工具时用中文顿号列举，措辞固定（前端不解析，仅展示）。

### 3. 克隆 `POST /api/v1/agents/{agent_id}/clone`（US2）

无请求体。产物复制源 Agent 的 10 个配置字段，名称按 FR-010 派生。

- `201` → `AgentSaveOut`
- `404 NOT_FOUND / "Agent 不存在"`：不存在或不属于自己（同一措辞）
- `403 FORBIDDEN`：viewer

**名称派生**：`{源名称} 副本`；若同所有者下已存在（含已归档），
依次尝试 `{源名称} 副本2`、`{源名称} 副本3`…（上限 10 次尝试；
基名超长时先截断到 `100 - len(" 副本10")`）。10 次仍撞名 → `409 CONFLICT`。

**warning 复用（FR-012）**：源 Agent `egress_mode=restricted` 且无启用的 allow 规则时，
`warning` 与 `POST /agents` 的文案逐字相同：
`"限制模式下尚无启用的允许规则，运行时全部出站被拒。"`

**克隆不复制**（FR-011，响应体无体现、由集成测试断言）：
运行/步骤/制品、组成员关系、外部工具授权、出站规则。

---

## 4. 内部契约（`backend/app/services/agent_template_service.py`）

### 4.1 `instantiate(db, *, template, payload) -> tuple[Agent, list[str]]`

从模板求出一个待落库的 Agent：先取模板快照，再用 `payload` 的非 unset 字段覆盖；
对 `tools` 做启用态过滤，返回 `(agent_draft, filtered_tools)`。
**不做 commit**（端点层统一提交，与 003 审计改造后的「service 只 flush」一致）。

### 4.2 `clone_agent(db, *, source, owner_id) -> Agent`

拷贝 10 个配置字段，派生名称；同样只 flush。
名称派生依赖一次 `SELECT name FROM agents WHERE owner_id=?` 的**已用名集合**
（含归档），在请求事务内完成——不为并发改名加锁（research 未验证项 #3）。

### 4.3 `save_as_template(db, *, agent, name, description) -> AgentTemplate`

快照 10 个字段；重名抛 `AppError("CONFLICT", "同名模板已存在")`。

### 4.4 审计动作（FR-019，全部 `strict=True`）

| action | target_type | 触发点 | detail 至少包含 |
|---|---|---|---|
| `agent_template_create` | `agent_template` | 1.5 | `name`、`source_agent_id`、`is_builtin=false` |
| `agent_template_update` | `agent_template` | 1.3 | `name`、改动字段名列表 |
| `agent_template_delete` | `agent_template` | 1.4 | `name` |
| `agent_create_from_template` | `agent` | 2 | `name`、`template_id`、`filtered_tools` |
| `agent_clone` | `agent` | 3 | `name`、`source_agent_id` |

种子脚本注入内置模板**不写审计**（系统行为，无操作者；与 0001 建表不写审计同理）。

### 4.5 种子脚本契约（`backend/scripts/seed_agent_templates.py`）

```
用法: .venv/bin/python scripts/seed_agent_templates.py [--dry-run]
```

- 幂等：按 `(owner_id IS NULL, name)` 定位，存在则跳过（**不更新**），不存在才插入。
- `model` 取 `settings.llm_model`（即 `LLM_MODEL`，缺省回落到 schema 默认值）。
- `tools` 只取工具目录中当前**启用**的工具；目录为空时该模板 `tools=[]` 并在 stdout 提示。
- 输出每个模板的 `name / model / tools` 到 stdout，供部署记录。
- 内置模板数量固定 4 个（spec Assumptions），`--dry-run` 只打印将插入哪些。

---

## 5. 字段表：`AgentCreate` ↔ `AgentTemplateCreate/Update`

两套 schema **共享字段定义与取值范围**，不允许各写一份默认值。
实现方式：`app/schemas/agent_template.py` 从 `app/schemas/agent.py` 导入
`ALLOWED_TOOLS` 与工具校验器，逐字段复制 `Field(...)` 约束并在单测中
交叉断言两边约束一致（防漂移测试，见 tasks.md）。

| 字段 | Agent | 模板 | 取值范围 / 默认 |
|---|---|---|---|
| `name` | ✅ 必填 | ✅ 必填 | 1..100；模板额外要求所有者下唯一 |
| `description` | 可空 | 可空 | — |
| `system_prompt` | ✅ 必填 | ✅ 必填 | ≥1（DB CHECK `<> ''`） |
| `model` | 默认 `gpt-4o-mini` | 默认同左 | ≤100；可用性不在契约层校验 |
| `temperature` | 0.7 | 同左 | 0.0..2.0 |
| `max_steps` | 10 | 同左 | 1..100 |
| `max_run_seconds` | 1800 | 同左 | 1..86400（前端表单默认 300，属展示层选择） |
| `tools` | `[]` | 同左 | 必须在 `CATALOG` 内；模板**保存时**校验，**创建 Agent 时**过滤 |
| `cost_limit_cents` | 10000 | 同左 | ≥1 |
| `egress_mode` | `default` | 同左 | `default` \| `restricted` |
| `status` | `active`/`archived` | **无此字段** | 模板没有状态机 |
| `source_template_id` / `source_agent_id` | 可空（新） | **无** | 仅 Agent 侧 |

**关键差异（只有两处）**：

1. `tools` 的处理时机：`AgentCreate` / `AgentTemplateCreate` **保存即拒**（不在目录 → 422；
   在目录但未启用 → 400 `validate_grants`），而**用模板创建 Agent 时**，模板里未启用的工具
   被过滤 + warning（FR-008）。即：模板**保存时**从严，模板**实例化时**从宽。
   注意「从严」不是白名单外一种错误，而是两种（422 / 400），与既有 Agent 创建完全一致。
2. `name` 唯一性：Agent **无**唯一约束（既有设计，允许同名）；模板 **有**
   （§1.3/1.5 的 409）。

---

## 6. 与既有契约的交叉点

| 交叉点 | 约定 |
|---|---|
| `AgentSaveOut.warning` | 复用，不新增字段；从模板创建的 warning 只报「被过滤的工具」 |
| `GET /agents` 列表 | **不返回模板**（模板在独立表），看板拖拽目标不受影响 |
| 组添加成员 (`POST /groups/{id}/members`) | **不接受模板 ID**；只能选真实 Agent |
| 调度目标（004） | 仍指向 Agent/组，**不指向模板**（spec 明确排除） |
| 审计查询 (`GET /audit-logs`) | 新动作天然可查，无需改契约 |
| 工具目录 (`GET /tools`) | 模板实例化时的启用态唯一事实源 |
