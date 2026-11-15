# Contracts: 沙箱代码执行与工具授权（Sandboxed Tools）

**Phase 1 产出**。既有契约见 `specs/001-core-workflow/contracts/api.md`，本文件只写新增与变更项；未提及的端点保持原样。

所有端点前缀 `/api/v1`，认证方式沿用 Bearer access token。

## 变更摘要

| 变更 | 端点/契约 | 说明 |
|---|---|---|
| 新增 | `GET /tools` | 工具目录（前端与校验的单一来源） |
| 变更 | `POST/PATCH /agents` | 新增 `egress_mode` 字段；`tools` 校验改读目录 |
| 新增 | `/agents/{id}/egress-rules` | Agent 级出站允许清单（限制模式用） |
| 变更 | `GET /runs/{id}` | 响应新增 `executions` 摘要数组；`artifacts` 每项新增 `sha256` |
| 新增 | `GET /runs/{id}/executions/{eid}` | 单次执行详情（代码、输出、资源、失败分类） |
| 新增 | `GET /runs/{id}/executions/{eid}/egress` | 该次执行的出站访问记录 |
| 新增 | `/admin/egress-blacklist` | 全局黑名单维护（仅 admin） |
| 新增 | `GET /admin/egress-accesses` | 出站访问记录检索（仅 admin） |
| 变更 | `WS /ws/runs/{id}` | 新增 5 类事件 |
| 新增 | 工具调用内部契约 | 5 个工具的 schema 与结果结构（§内部契约） |

## 工具目录

### GET /tools

返回**代码 CATALOG 与 DB 中显式 enabled=true 行的交集**（FR-001）。缺行或禁用均不暴露，任何登录用户可读。定义/schema 仍由代码统一提供；禁用/删除工具时修剪 Agent 当前勾选，已有运行的历史快照不改写。运行中每次派发前仍检查当前授权与启用状态。

```json
{"items": [
  {"name": "web_search", "display_name": "联网搜索", "kind": "builtin",
   "description": "联网搜索公开信息…", "param_schema": {"type":"object","properties":{...}},
   "timeout_seconds": 30},
  {"name": "run_python", "display_name": "执行 Python", "kind": "sandbox",
   "description": "在隔离环境中运行 Python 脚本…", "param_schema": {...}, "timeout_seconds": 120}
]}
```

- `kind='sandbox'` 的工具在隔离设施不可用时仍然列出（能力存在，运行时快速失败并给出 `SANDBOX_UNAVAILABLE` 原因）。
- 前端 MUST 用本接口渲染工具选项，MUST NOT 硬编码清单。

## Agent 契约变更

### POST /agents · PATCH /agents/{id}

请求体新增/变更字段：

```json
{"tools": ["web_search", "run_python"], "egress_mode": "restricted"}
```

- `tools`：必须是 `GET /tools` 中**已启用**的工具名；含未知/已下线工具 → 400 `INVALID_INPUT`，`detail` 列出非法项与可选清单。
- `egress_mode`：`default`（默认）| `restricted`。切换为 `restricted` 时若该 Agent 无任何 allow 规则，允许保存但**运行时全部出站被拒**；接口响应附 `warning` 提示。
- 响应新增 `egress_mode`。

## Agent 级出站规则

资源归属：仅 Agent 的 `owner_id` 或 admin 可操作。

### GET /agents/{id}/egress-rules

```json
{"items": [{"id": "...", "target_type": "domain", "target": "api.openai.com",
            "port_range": "443", "note": "调模型接口", "created_at": "..."}], "egress_mode": "restricted"}
```

### POST /agents/{id}/egress-rules

```json
{"target_type": "domain", "target": "pypi.org", "port_range": null, "note": "装依赖"}
```

→ 201。校验：`target_type` ∈ domain/cidr/ip；domain 支持单个前导 `*.`；`port_range` 形如 `443` 或 `8000-8100`；同一 Agent 内重复目标 → 409 `CONFLICT`。

**域名规则的运行时边界**：执行私有可信 DNS mediator 验证精确/通配域名及完整 CNAME 链，在答复前原子写入按规则、IP 族、端口与 TTL 隔离的 nft 允许/拒绝集合。全局黑名单优先，绑定过期、helper 健康租约失效或不安全/无法验证的答案均 fail-closed，已建立连接也重查。默认模式存在全局域名 deny 时，未受控解析绑定的直连 IP 拒绝。授权是目的 IP+端口，不是 TLS/SNI/HTTP Host 身份验证，共享 IP 和未观察别名的限制仍存在。Docker Desktop Linux VM 专项 18 项已通过；原生 GitHub 整栈结果另行记录，不以 VM 专项代替。

**不允许**通过本端点放宽全局黑名单：若 `target` 命中黑名单（如同一网段），返回 400 `INVALID_INPUT`，`detail` 说明「该目标被全局黑名单覆盖」。

### DELETE /agents/{id}/egress-rules/{rule_id}

→ 204。

## 运行详情契约变更

### GET /runs/{id}（响应新增）

`tool_grants_snapshot` 为初始授权工具名数组，`[]` 表示无工具，SQL NULL 经 API 表示 `null`（历史未记录或 router 待选）。直接/子运行在入队前捕获，router 首次确定 Agent 时捕获；非 NULL 快照不可改写，重试保留。实际调用权限只能进一步收窄，不因 Agent 后续新增勾选而升级。

```json
{"id": "...", "status": "running", "tool_grants_snapshot": ["run_python"],
 "executions": [
   {"id": "...", "step_seq": 4, "language": "python", "status": "ok",
    "exit_code": 0, "duration_ms": 1820, "stdout_truncated": false, "artifacts": 2}
 ]}
```

`executions` 只含摘要（不含 `code`/`stdout`/`stderr`），避免详情接口体积膨胀（R-04）。

### GET /runs/{id}/executions/{eid}

```json
{"id": "...", "run_id": "...", "step_seq": 4, "language": "python",
 "status": "failed", "error_category": "exit_code", "error_message": "脚本以退出码 1 结束",
 "exit_code": 1, "code": "import pandas as pd\n…", "code_sha256": "…",
 "stdout": "…", "stderr": "Traceback …", "stdout_truncated": true, "stdout_full_bytes": 2097152,
 "duration_ms": 4210, "cpu_ms": 3100, "mem_peak_bytes": 148897792,
 "image": "aw-sandbox-python:3.12",
 "limits": {"timeout_s": 120, "cpu": 1.0, "mem_mb": 512, "disk_mb": 256, "pids": 128},
 "artifacts": [{"id": "...", "filename": "result.csv", "size_bytes": 2048}],
 "artifact_truncated": {"reason": "files", "message": "..."} | null}
```

权限：Run 的 `owner_id` 或 admin；其他用户 403（沿用 001 的越权修复口径）。

### GET /runs/{id}/executions/{eid}/egress

```json
{"items": [
  {"target_ip": "151.101.0.223", "target_domain": "pypi.org", "port": 443,
   "action": "allow", "bytes_out": 10240, "occurred_at": "..."},
  {"target_ip": "169.254.169.254", "target_domain": null, "port": 80,
   "action": "deny", "matched_rule_id": "...", "occurred_at": "..."}
]}
```

## 管理后台（admin）

### GET /admin/egress-blacklist

```json
{"items": [{"id": "...", "target_type": "cidr", "target": "169.254.0.0/16",
            "note": "云元数据服务，禁止访问", "enabled": true, "created_by": "..."}]}
```

### POST /admin/egress-blacklist

```json
{"target_type": "cidr", "target": "10.0.0.0/8", "port_range": null, "note": "内网"}
```

→ 201。仅 `action='deny'`。重复目标 → 409。**内置项不可删除**：`169.254.0.0/16`、`127.0.0.0/8`、`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16` 首次迁移时写入并标记 `builtin=true`，删除接口对它们返回 400。

### DELETE /admin/egress-blacklist/{id} → 204 / 400（内置项）

### GET /admin/egress-accesses

参数：`?run_id=&agent_id=&action=allow|deny&from=&to=&page=`。
```json
{"items": […], "total": 1283, "summary": {"allow": 1200, "deny": 83, "ip_direct": 12}}
```

`ip_direct` 统计「未反查到域名」的直连次数——这是审计时的可疑信号（R-02 已知缺口）。

## WebSocket 事件（新增）

沿用 `WS /ws/runs/{id}`，新增：

```json
{"type": "tool_denied", "tool": "run_shell", "message": "该 Agent 未授权工具 run_shell", "ts": "..."}
{"type": "sandbox_started", "execution_id": "...", "step_seq": 4, "language": "python", "limits": {...}}
{"type": "sandbox_output", "execution_id": "...", "stream": "stdout", "chunk": "…", "ts": "..."}
{"type": "sandbox_finished", "execution_id": "...", "status": "ok", "exit_code": 0, "duration_ms": 1820, "artifacts": 2}
{"type": "egress_denied", "execution_id": "...", "target_ip": "169.254.169.254", "port": 80, "reason": "命中全局黑名单"}
```

`sandbox_output` 为增量块（≤4KB/条），前端按执行聚合；不重复推送 `step_update` 的全量 output（避免体积翻倍）。

## 内部契约：工具 schema 与结果结构

`app/executors/catalog.py` 的 5 个工具（model-visible schema）：

| name | kind | 参数 | 说明 |
|---|---|---|---|
| `web_search` | builtin | `{query: string}` | 联网搜索，返回结果列表 |
| `read_url` | builtin | `{url: string}` | 抓取网页正文（SSRF 防护沿用 001） |
| `calculate` | builtin | `{expression: string}` | 安全表达式求值 |
| `run_python` | sandbox | `{code: string, timeout_s?: int}` | 隔离环境执行 Python |
| `run_shell` | sandbox | `{command: string, timeout_s?: int}` | 隔离环境执行 shell |

沙箱工具的**统一结果结构**（回填给 LLM 与落库）：

```json
{"ok": false, "status": "failed", "error_category": "exit_code", "exit_code": 1,
 "stdout": "…（尾部 ≤1MB）", "stderr": "…", "stdout_truncated": true,
 "duration_ms": 4210, "artifacts": [{"filename": "out.csv", "size_bytes": 2048}],
 "message": "脚本以退出码 1 结束。stderr 末尾：Traceback …"}
```

**硬性约定**：失败 MUST 以 `ok:false` 返回而非抛异常（`runner.py` 现有的 `except Exception` 兜底会把异常压成一句 `error`，丢掉 `exit_code`/`stdout`，模型无法据此修正——这是 FR-013「不得当作成功、也不得压成不可用信息」的直接要求）。
未授权工具的返回（由 runner 层生成，不经执行器）：

```json
{"ok": false, "denied": true, "status": "denied",
 "message": "工具 run_shell 未被该 Agent 授权。可用工具：web_search, run_python。"}
```

## 错误码变更

沿用 001 的错误码表，新增/明确：

| HTTP | code | 场景 |
|---|---|---|
| 429 | SANDBOX_QUOTA_EXCEEDED | 沙箱并发已满且排队超时（FR-025） |
| 503 | SANDBOX_UNAVAILABLE | 隔离设施不可用（镜像缺失/守护不可达）（FR-027） |
| 400 | EGRESS_TARGET_INVALID | 出站规则目标非法，或被全局黑名单覆盖 |
| 409 | EGRESS_RULE_DUPLICATE | 同一 Agent 内重复规则 |
| 403 | TOOL_NOT_AUTHORIZED | 仅内部使用（工具被拒时以 `tool_denied` 事件 + step output 表达，不作为 HTTP 返回） |

> `SANDBOX_QUOTA_EXCEEDED` / `SANDBOX_UNAVAILABLE` 作用于**工具调用**，不作用于 HTTP 请求；其可读文案经 `sandbox_executions.error_message` 与 `sandbox_finished` 事件呈现给用户，并在 step output 里回填给模型。

## 当前实现与原验收契约差异（2026-11-15）

下列项目区分原验收目标、当前实际安全边界与验证范围；契约同步本身不替代 T076 整栈验收，也不把已知限制自动豁免。真实容器及原生 CI 证据见 `../docker-verification.md`。

- **身份与归属**：当前 Run、执行详情、制品下载只允许 owner，其他用户（包括非 owner admin）返回 404，匿名 401；不是上文 owner/admin + 403。后端集成测试已覆盖；本轮受保护 HTTP 验收因测试管理员/邀请码授权被拒而未运行。
- **空 restricted 规则 warning（已对齐）**：Agent POST/PATCH 保存时，restricted 且无启用 allow 规则返回 `warning`；保存不被阻止。存在启用 allow 或 default 模式时 warning 为 null。
- **内置黑名单**：来自服务端配置常量，GET 返回合成 `builtin:*` 项，不是首次迁移写入的 DB 行。删除内置项返回 400；其访问记录 `matched_rule_id=null`，自定义规则命中使用真实 UUID。
- **访问检索（已对齐）**：支持 run_id、agent_id、action、`from`、`to`、page、page_size。时间区间为半开 `[from,to)`，时间必须带时区，summary 与 items 使用相同筛选，且 summary 不受分页影响。
- **出网事实**：固定 AF_PACKET 包头 watcher 在用户进程前就绪，以内核 source-IP BPF 和 bridge-master/veth 归属校验捕获 pre-defrag 出站。TCP 按源端口/初始序号与生命周期区分连接；UDP 按五元组流归并，不声称能从无连接协议识别同五元组 socket 重开。IPv4/IPv6 目标均记录；分片、扩展链及不支持的路由协议在内核拒绝，并记录真实 IP、port=0。丢包、事件预算耗尽或日志截断不能静默当作成功。`bytes_out` 仍为 null，不采正文、不伪造传输字节。
- **域名策略**：精确/通配/CNAME 域名策略已由执行私有 DNS 与 TTL/端口 nft 集合实现；共享 dnsmasq 日志不作为授权依据。DNS 策略拒绝也产生归属明确的 DNS 目标端口 53 事实，不伪造从未发生的后续 TCP 连接。原生整栈结果与专项 VM 结果分开，且不承诺 TLS/共享 IP 身份识别或任意高负载下的零丢包。
- **拒绝事件**：`egress_denied` 带 execution_id、target_ip、target_domain、port、matched_rule_id，以及通用可读 reason（不披露具体内部规则细节）。
- **输出事件（实现已接线，端到端仍待验）**：容器运行时 attach 后增量发布；按 UTF-8 字节切片，每条不超过 4096 bytes；输出尾部与实时发布预算均有界。集成测试验证 started/output/finished 的顺序与 started 唯一，但跨服务 WebSocket protected live E2E 尚未执行。
- **制品截断提示（已对齐）**：执行详情增加 `artifact_truncated: {reason,message} | null`；仅当 Step.output 中 execution_id 与详情匹配时投影，旧数据不猜测归属。LLM/Step/API/前端均能看到截断原因，执行成功状态不因此改为失败。
