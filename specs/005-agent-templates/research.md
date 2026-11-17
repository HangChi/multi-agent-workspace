# Research: Agent 模板与一键克隆

**Branch**: `feat/005-agent-templates` | **Date**: 2026-11-18 | **Spec**: [spec.md](spec.md)

本文件记录 005 的关键技术决策。每条都能回答「为什么更简单的做法不行」——
没有更简单可行方案的决策不写在这里。

## R-01: 模板存快照，不存引用

**决策**：`agent_templates` 表保存 Agent 配置的**完整快照**；从模板创建的 Agent 是一次性
拷贝，之后模板与 Agent 双向隔离。

**为什么不引用**：引用式（Agent 持有一个 `template_id`，字段实时从模板读）会让「改模板」
同时改掉 N 个 Agent 的提示词、模型与成本上限。用户改模板时并不知道有多少 Agent 在用它，
排障时看到的配置与历史运行时的配置也对不上。004 已经为同一类问题立过纪律：
「修改调度 MUST NOT 改写已产生的需求」（FR-020）。模板是同一问题的另一个切面，
采用同一解法：**写时拷贝，之后各不相干**。

**代价**：模板不会自动获得「改进」，用户需要主动重新另存。这是可接受的——
复用的是「一个已验证的起点」，不是「一份持续同步的配置」。

## R-02: 内置模板用种子脚本注入，不写进迁移

**决策**：内置模板由 `backend/scripts/seed_agent_templates.py` 幂等注入；
迁移 0009 只建表。

**为什么不放迁移**：

1. **仓库既有约定**：`backend/alembic/versions/` 下没有任何迁移导入 `app.*`
   （已实测 `grep -l "from app"` 为空）。迁移应能在无应用上下文、无 `.env` 的环境运行。
2. **模型必须跟随端点**：内置模板的模型取 `LLM_MODEL`（`app/core/config.py`）。
   若硬编码进迁移，换 LLM 端点后内置模板会全部指向不存在的模型——
   这正是 2026-11-18 接入新端点时的坑（`No available channel for model gpt-4o-mini`）。
   迁移写死 `gpt-4o-mini` 会把这个坑固化进 schema。

**为什么不放启动钩子**：启动时跑种子会让每次 API 启动多一次数据库往返，
且失败时难以与「启动失败」区分。脚本由部署流程显式调用（`deploy/README.md` 登记），
与 `scripts/bootstrap.py` 同一形态。

## R-03: 工具失效在创建时过滤并提示，不让模板作废

**决策**：使用模板创建 Agent 时，对 `tools` 逐项按工具目录校验；已下线/未启用的项被过滤，
被过滤的工具名随 `AgentSaveOut.warning` 返回。创建本身 MUST 成功。

**为什么不是创建失败**：工具会随版本下线（002 FR-005 就有「工具下线时从存量 Agent 移除」
的机制）。若模板因一个工具下线就整体作废，用户看到的是一次没有说明的失败，
只能手工逐项排查。过滤 + 明示是唯一既不让模板作废、又不静默改变用户意图的组合。

**实现落点**：复用 `agent_service.validate_grants()`（存在性）与
`tool_service` 的启用态判定；过滤逻辑放在 `agent_template_service.instantiate()` 内，
不修改既有 `validate_grants` 的语义（手工创建路径保持「白名单外即 400」）。
两条路径的差异本身是需求：FR-008（模板路径过滤）vs FR-009（同一套其余校验）。

## R-04: 克隆不复制实例级数据，警告复用既有 warning 字段

**决策**：克隆只拷贝 8 个配置字段；运行/步骤/制品、组成员关系、外部工具授权、出站规则
一律不复制。源 Agent 为 `restricted` 且无启用 allow 规则时，克隆成功并在 `warning`
里带上与 `POST /agents` 完全一致的文案。

**为什么不复制授权**：外部工具授权关联凭证（Fernet 加密存储），出站规则是网络边界。
复制它们会得到一个「界面显示已授权、跑起来才被拒」的 Agent，用户要为这个假象付出一次
失败的运行成本。不复制 + 提示，成本是一次点击。

**为什么不新增响应字段**：`AgentSaveOut.warning` 已存在且前端已有展示路径
（AgentForm/AgentPage 的 warning 渲染）。复用字段比加字段便宜，且两种警告对用户是
同一类信息「保存成功了，但有个事你要知道」。

## R-05: 模板名唯一约束要分开处理「自己的」与「内置的」

**决策**：

- 自己的模板：数据库 `UNIQUE(owner_id, name)`。
- 内置模板（`owner_id IS NULL`）：PostgreSQL 的唯一约束在含 NULL 时**不生效**
  （NULL 与 NULL 不相等），因此改用**部分唯一索引**
  `CREATE UNIQUE INDEX uq_agent_templates_builtin_name ON agent_templates(name) WHERE owner_id IS NULL`。

**为什么值得单独说**：这是本特性唯一一处「看起来加了唯一约束、其实没约束」的坑。
若只写 `UNIQUE(owner_id, name)` 就收工，种子脚本重复执行会插入一堆同名内置模板，
而 SC-008「重复执行种子后模板数不变」会在生产才失败。模型层与迁移层都要显式声明该索引。

## R-06: Agent 表加两个可空来源外键，都 ON DELETE SET NULL

**决策**：`agents.source_template_id`、`agents.source_agent_id`，均可空，
外键 `ON DELETE SET NULL`，各建普通索引。

**为什么是 SET NULL 而不是 CASCADE/RESTRICT**：删模板/删源 Agent 都不该带走
已创建的 Agent（FR-021）；反过来也不该因为「有 Agent 引用它」就禁止删除模板——
那会让模板永远删不掉。SET NULL 让来源退化为「未知」，Agent 本身完好。

**为什么不合并成一个 `source_*` 多态字段**：两个语义不同的来源（模板 / 克隆的源 Agent）
合并成一个 `source_type + source_id` 会让外键约束消失，退化成应用层约定。
本仓库对多态目标已有先例（`schedules.target_type/target_id`，因为组与 Agent 确实是两类
不同资源），但这里是同一类资源的两种来源，两个外键更便宜也更安全。

## R-07: 前端新增独立模板页 + Agent 页两个入口，复用 AgentForm

**决策**：

- 新增路由 `/agent-templates`（模板管理：内置区 + 我的模板区，查看/编辑/删除/另存为模板/用模板创建）。
- `AgentPage` 顶部加「从模板创建」主按钮；`AgentListItem` 加「克隆」「另存为模板」两个动作。
- 创建表单**复用 `AgentForm`**，通过 `initial` 预填模板值。

**为什么不把模板管理塞进 AgentPage 的第四个 tab**：AgentPage 已有
all/active/archived 三态过滤 + 分页 + 归档/删除语义，再塞一套「模板不可归档、
内置只读、删除不影响 Agent」的语义进去，会让一个页面同时承载两套生命周期。
独立页面让两套语义各自清晰，也与 `SchedulesPage`（004）的形态一致。

**为什么不新写一个模板表单**：模板字段与 Agent 字段逐字相同（10 个配置字段）。
新写一份表单 = 两份会漂移的校验。复用 `AgentForm` + `initial` 是零漂移方案。

## R-08: 不为模板建独立索引族，对齐 schedules 的最小索引集

**决策**：`agent_templates` 只建两个索引：`(owner_id, created_at)` 复合
（成员列表：按创建时间倒序取自己的）+ 内置部分唯一索引（R-05）。

**为什么不加 `is_builtin`/`q` 相关索引**：模板量级与 Agent 同阶（数十到数百），
列表查询全部走 `owner_id` 前缀；`q` 用 `ilike` 与 `agents` 一致，不加索引。
额外索引只会让每次写入变慢，且在本量级没有可观测收益。

## 未验证 / 待实现阶段确认的点

- **内置模板的提示词质量**：4 个模板的文案要「直接用当前端点跑出可用结果」。
  实现阶段用 Mock 网关 + 真实端点各跑一次再定稿，不在 plan 阶段拍死文案。
- **`max_run_seconds` 前端默认值**：`AgentForm` 现有默认 300（schema 默认 1800）。
  模板沿用表单默认，不在模板层另设默认，避免两套默认值漂移。
- **克隆名称撞车的竞争**：两个并发克隆同一 Agent 可能算出同一个「副本」名。
  Agent 名无唯一约束（既有设计），因此并发同名不会报错，只是出现两个同名 Agent；
  实现阶段确认前端列表能区分（列表显示创建时间），不为并发改名加锁。
