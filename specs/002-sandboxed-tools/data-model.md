# Data Model: 沙箱代码执行与工具授权（Sandboxed Tools）

**Phase 1 产出**。既有 10 张表见 `specs/001-core-workflow/data-model.md`，本文件只写新增/变更部分。

## 实体与字段

### tools（新增）

工具目录的持久化面。**字段值的唯一事实源是 `app/executors/catalog.py`**，本表由 seed 迁移写入，只承载运营侧可变部分（启用开关、展示文案）。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| name | varchar(50) | PK | `web_search` / `read_url` / `calculate` / `run_python` / `run_shell` |
| display_name | varchar(100) | NOT NULL | 界面展示名（中文） |
| description | text | NOT NULL | 给模型看的说明（与 catalog 一致） |
| kind | enum('builtin','sandbox') | NOT NULL | builtin=进程内执行器；sandbox=需隔离容器 |
| enabled | boolean | NOT NULL, DEFAULT true | 关闭后不出现在可选目录，运行时报未授权 |
| param_schema | jsonb | NOT NULL | OpenAI function 参数 schema |
| timeout_seconds | int | NOT NULL, DEFAULT 30 | 单次调用上限；sandbox 类由执行配额覆盖 |
| updated_at | timestamptz | NOT NULL | |

**索引**: `(enabled)`。

### agents（变更）

| 变更 | 字段 | 类型 | 说明 |
|---|---|---|---|
| 新增 | egress_mode | enum('default','restricted') | NOT NULL, DEFAULT 'default' | `default`=放行并记录；`restricted`=仅允许 `egress_rules` 中该 Agent 的 allow 项 |
| 不变 | tools | jsonb | 仍是工具名数组（spec 的 ToolGrant 语义由它承担，见 D-2） |

### sandbox_executions（新增）

一次脚本执行的事实记录，与 `steps` 1:1。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| run_id | UUID | FK→runs, NOT NULL | |
| step_id | UUID | FK→steps, NOT NULL, **UNIQUE** | 与 step 一一对应 |
| agent_id | UUID | FK→agents, NOT NULL | 冗余，供按 Agent 统计 |
| language | enum('python','shell') | NOT NULL | |
| code | text | NOT NULL | 脚本全文（审计与复现；已按 `sandbox_code_max_bytes` 截断） |
| code_sha256 | char(64) | NOT NULL | 内容指纹 |
| status | enum('running','ok','failed','timeout','resource','policy','infra') | NOT NULL, DEFAULT 'running' | 失败分类直接落库，支撑 SC-008 |
| exit_code | int | NULL | 非零退出码 |
| stdout | text | NULL | 截断后保留尾部（≤`sandbox_stdout_max_bytes`） |
| stderr | text | NULL | 同上 |
| stdout_truncated | boolean | NOT NULL, DEFAULT false | |
| stdout_full_bytes | bigint | NOT NULL, DEFAULT 0 | 原始长度，供「已截断」提示 |
| duration_ms | int | NOT NULL, DEFAULT 0 | |
| cpu_ms | int | NULL | 资源峰值（从 cgroup 读取，读不到为 NULL） |
| mem_peak_bytes | bigint | NULL | |
| image | varchar(120) | NOT NULL | 实际使用的镜像 tag |
| limits | jsonb | NOT NULL | `{timeout_s, cpu, mem_mb, disk_mb, pids}` 快照 |
| error_category | varchar(40) | NULL | `timeout / oom / pids / disk / policy / infra / exit_code` |
| error_message | text | NULL | 面向用户的可读原因 |
| created_at / finished_at | timestamptz | | |

**索引**: `(run_id)`、`(agent_id, created_at)`（配额与统计）、`(status)`。
**保留策略**: 按 `sandbox_executions` 的 `created_at` 保留 90 天，`code`/`stdout`/`stderr` 到期清空（只留元数据与指纹），避免表无限膨胀。

### egress_rules（新增）

出站策略。一条规则要么是全局黑名单项，要么是某个 Agent 的允许项。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| scope | enum('global','agent') | NOT NULL | |
| agent_id | UUID | FK→agents, NULL | scope='agent' 时必填 |
| action | enum('deny','allow') | NOT NULL | 全局只允许 deny；agent 只允许 allow |
| target_type | enum('domain','cidr','ip') | NOT NULL | |
| target | varchar(255) | NOT NULL | `pypi.org` / `10.0.0.0/8` / `169.254.169.254`；domain 支持 `*.example.com` |
| port_range | varchar(20) | NULL | 空 = 全部端口；`443` / `8000-8100` |
| note | varchar(200) | NULL | 为什么放行/拦截 |
| enabled | boolean | NOT NULL, DEFAULT true | |
| created_by | UUID | FK→users, NOT NULL | 全局项仅 admin |
| created_at | timestamptz | NOT NULL | |

**唯一约束**: `(scope, agent_id, action, target_type, target, port_range)`。
**索引**: `(scope, agent_id)`、`(enabled)`。

### egress_accesses（新增）

一次对外连接的事实。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| run_id | UUID | FK→runs, NOT NULL | |
| execution_id | UUID | FK→sandbox_executions, NULL | 非沙箱场景（如 read_url）也可记录 |
| agent_id | UUID | FK→agents, NOT NULL | |
| user_id | UUID | FK→users, NOT NULL | 归属校验与审计 |
| target_ip | inet | NOT NULL | 真实连接目标 IP |
| target_domain | varchar(255) | NULL | DNS 反查结果；`ip-direct` 表示直连 IP 未反查到域名 |
| port | int | NOT NULL | |
| protocol | varchar(10) | NOT NULL, DEFAULT 'tcp' | tcp / udp |
| action | enum('allow','deny') | NOT NULL | |
| matched_rule_id | UUID | FK→egress_rules, NULL | 命中的规则（deny 时必填） |
| bytes_out | bigint | NULL | conntrack 快照的出口字节数 |
| occurred_at | timestamptz | NOT NULL | |

**索引**: `(run_id)`、`(agent_id, occurred_at)`、`(action, occurred_at)`。
**写入方式**: 执行结束时按批插入（一次执行通常几十条），不做逐条实时写，避免高频写放大（见 D-3）。

### artifacts（复用，无结构变更）

新增约定：`object_key` 前缀为 `artifacts/{run_id}/{execution_id}/{filename}`，`step_id` 指向产生它的工具步骤。下载与归属校验沿用既有实现。

## 校验不变量

1. `sandbox_executions.step_id` 唯一：一次执行只对应一个步骤，一个步骤最多一次执行。
2. `sandbox_executions.run_id` 必须与所属 step 的 `run_id` 一致。
3. `sandbox_executions.status` 与所属 `steps.status` 必须一致（`ok→completed`，其余→`failed`）。
4. `egress_rules`：`scope='agent'` 必须有 `agent_id` 且 `action='allow'`；`scope='global'` 必须 `agent_id IS NULL` 且 `action='deny'`。
5. **求值顺序固定**：全局 deny 优先于任何 agent allow；`egress_mode='restricted'` 且无匹配 allow → deny。任何 Agent 配置都不能放宽全局黑名单（FR-022）。
6. `egress_rules.target` 为 `cidr` 时必须能被解析为合法网段；`ip` 时必须合法 IP。
7. `egress_accesses.target_domain` 为 `ip-direct` 时 `matched_rule_id` 为 NULL 且 `action` 只能为 `allow`（直连 IP 本身不拦截，除非命中 cidr 黑名单）。
8. 同一 `(execution_id, object_key)` 在 artifacts 中唯一：同名文件由执行器追加序号，不覆盖。
9. 并发占用：同一时刻 `status='running'` 的 `sandbox_executions` 数量 ≤ `SBX_MAX_CONCURRENCY`（Redis 信号量保证，DB 侧作为校验断言）。
10. `tools.enabled=false` 的工具不出现在 `GET /tools`，且在运行期按「未授权」处理（不是「未知工具」）。

## 状态迁移

### sandbox_executions.status

```
running ──(退出码 0)──► ok
running ──(退出码非零)──► failed        error_category=exit_code
running ──(超时被杀)──► timeout         error_category=timeout
running ──(OOM / pids / 磁盘)──► resource  error_category=oom|pids|disk
running ──(命中黑名单策略)──► policy      error_category=policy
running ──(守护不可用/镜像缺失/网络创建失败)──► infra  error_category=infra
```

**终态不可迁移**；`infra` 与 `timeout` 允许由用户重试（新建一条执行记录，不复用旧记录）。
**记录时机**：`running` 在容器启动后写入（拿到 container id 之前失败直接写 `infra`，不产生 running 记录）。

### agents.egress_mode

```
default ⇄ restricted   （成员任意切换；切换不影响进行中的执行——执行开始时快照）
```

## 源→产物字段映射

| 源（spec FR） | 产物（表字段） | 说明 |
|---|---|---|
| FR-001 目录真实性 | tools（由 catalog.py seed） | 每个可选项都有执行器 |
| FR-002/004 按勾选注入 | agents.tools → 运行时 executors 字典 | 空集即无工具 |
| FR-003 越权拒绝 | steps.output.error + audit_logs(action='tool_denied') | 拒绝记录 |
| FR-005 下线兼容 | 迁移日志 + 历史 steps 不改写 | 快照不可追溯 |
| FR-006~011 执行 | sandbox_executions（language/code/stdout/exit_code/limits） | |
| FR-009/010 超时与配额 | status=timeout/resource + error_category | |
| FR-012 输出截断 | stdout_truncated / stdout_full_bytes | |
| FR-013 结构化失败 | status + exit_code + error_category + error_message | |
| FR-014 取消即杀 | status='failed'(error_category='cancelled')，container kill | |
| FR-015~018 制品 | artifacts（object_key/filename/size/sha256→见 D-5） | |
| FR-019/020 出站 | egress_accesses | |
| FR-021/022 黑名单与限制模式 | egress_rules + agents.egress_mode | |
| FR-024/025 可观测与配额 | sandbox_executions 索引 + stats | |
| FR-026 审计 | audit_logs（action='sandbox_exec' / 'tool_denied' / 'egress_denied'） | |

## 偏差清单

- **D-1**: spec 未要求工具目录落库，本模型新增 `tools` 表。理由：需要「管理员可临时下线某工具」的运营能力，且前端需要单一来源（R-03）。表内容由代码常量 seed，代码仍是事实源。
- **D-2**: spec 的 Key Entity 写了 `ToolGrant`（独立实体），本模型**不建该表**，由既有 `agents.tools`（jsonb 数组）承担。理由：Agent 与工具的关联是纯多值属性、无额外字段（无授权时间/授权人需求），建表只增加一次 join；语义与 ToolGrant 等价。
- **D-3**: spec FR-020 要求记录「实际连接目标」，本模型记录到 `IP + port + 反查域名`，**不做 TLS 解密**，因此没有 URL 路径与请求体。这是 R-02 的已知缺口，已在运维文档写明。
- **D-4**: spec 未指定执行明细的落库位置，本模型新建 `sandbox_executions` 而非复用 `steps.output`（R-04 的容量与索引理由）。
- **D-5**: 既有 `artifacts` 表没有内容校验列（001 的 D-1 只覆盖附件元数据）。FR-017 要求「校验信息」，本特性在 `artifacts` 上加 `sha256 char(64) NULL`，对历史数据置空。这是对本特性的最小改动，未回写 001 文档（001 已全量验收，不追改）。
- **D-6**: spec 说「超过阈值后该步骤终止」的越权重复调用阈值未给数值，本模型取 `TOOL_DENY_THRESHOLD=5`（同一 run 内连续 5 次越权即中止该步），落为配置项而非硬编码。
