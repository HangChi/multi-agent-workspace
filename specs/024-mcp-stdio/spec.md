# Feature Specification: MCP stdio 传输（024）

**Status**: 部分实现（协议与进程层完成并验证；**未接进 003 的注册/发现/调用链**，理由见末节）

**Input**: 用户 2026-11-21「继续 A8」。003 明确「只支持 Streamable HTTP」。

## 问题陈述

社区里大量 MCP server 只有 stdio 入口：`npx -y @xxx/mcp`、`uvx xxx-mcp`、
`python -m xxx_mcp`。它们**不是一个服务**，而是一个进程：启动、在 stdin/stdout
上说 JSON-RPC、退出。

003 的实现全部建立在「MCP server 已经跑在某个 URL 上」这个前提：
`endpoint_url` 非空、`validate_endpoint` 做 SSRF 校验、调用走 `httpx`。
所以 stdio 不是「加一个分支」，而是给整个外部工具链路引入第二种传输形态。

## 本次交付范围（刻意收窄）

| 层 | 状态 |
|---|---|
| stdio JSON-RPC 协议（initialize / tools/list / tools/call） | ✅ 完成 |
| 子进程生命周期（启动 / 读取 / 关闭 / 回收 / 崩溃检测） | ✅ 完成 |
| 安全边界（命令白名单 / 不走 shell / 环境变量白名单 / 输出上限） | ✅ 完成 |
| 真实子进程端到端测试 | ✅ 8 例 |
| 接进 003 的 server 注册 / 校验发现 / executor 工厂 / 前端表单 | ❌ 未做（见末节） |

**为什么这样切**：协议与进程层是确定性最高、可独立验证的部分（用一个 fake
server 就能端到端跑）。而「接线」要动 003 的模型（`endpoint_url` 非空）、
校验（`validate_endpoint` 的 URL 语义）、工厂（`build_external_executors`）、
前端表单四处——这四处每一处都在服务稳定特性，改坏的症状是「已装的外部工具
突然不能用了」。分两步做，第一步的产出（`StdioMCPClient`）不会因为第二步的
设计变化而作废。

## 安全设计（三条底线）

1. **不走 shell**：`create_subprocess_exec(command, *args)`，参数以数组传递。
   `shell=True` 加用户可控参数等于命令注入（`; rm -rf /`）。
2. **命令白名单**：只允许配置里的可执行文件（默认 `npx / uvx / python /
   python3 / node`）。没有它，这个功能等价于「让任何 member 在 worker 容器里
   执行任意命令」。比对 `os.path.basename(command)`，`/bin/sh; rm -rf /`
   这类伪造会被拒。
3. **环境变量白名单**：只透传 `PATH / HOME / LANG / LC_ALL / TMPDIR / TZ /
   SYSTEMROOT`。否则 `DATABASE_URL`、`LLM_API_KEY` 会原样送进第三方进程。

另加两条运行时保护：单条响应 ≤2MB、握手 20s / 调用 60s 超时、stderr 只保留
尾部 4KB（诊断用，且必须持续读——不读会写满管道把子进程卡死）。

## 协议实现要点

- **换行分帧**：一行一条 JSON-RPC 消息。
- **跳过 notification**：server 可以主动发（`notifications/...`），读取时按
  「有没有 `id`」区分响应与通知，而不是当成错误。
- **id 校验**：响应的 `id` 必须与请求一致，否则协议错误。
- **错误分类**：传输层（进程退出/超时/超限）与协议层（非 JSON / id 不匹配）
  分开，`MCPToolError` 单列（工具自己返回 `isError`）。
- **退出检测**：stdout EOF = server 已退出，错误信息里带 stderr 尾部——
  否则用户只看到「失败了」，没有任何线索。

## 需求

- **FR-001** `StdioMCPClient` 与 `MCPClient`（HTTP）**接口一致**：
  `initialize()` / `list_tools()` / `call_tool(name, args)` / `cancel(id)`。
- **FR-002** async context manager；`close()` 走 stdin 关闭 → terminate →
  超时 kill，并 `await wait()` 回收（不留僵尸）。
- **FR-003** 命令白名单校验在**构造时**做（配置错误立刻暴露，不是运行时）。
- **FR-004** 环境变量白名单；调用方可传少量 override（如 `NPM_CONFIG_...`）。
- **FR-005** stderr 持续读取、只留尾部，崩溃时进错误信息。
- **FR-006** 响应超过 `max_response_bytes` 即报错（不把整个流读进内存）。

## 成功标准

- SC-001 真实子进程：握手 → 列工具 → 调用，三步通过。
- SC-002 `bash -c "rm -rf /"` 与 `/bin/sh; rm -rf /` 在构造时被拒。
- SC-003 server 启动即崩 → 抛传输错误且消息里带 stderr（"boom"）。
- SC-004 关闭后 `process.returncode` 非空（无僵尸）。
- SC-005 stdout 输出非 JSON → 协议错误（不是静默挂住）。

## 未接线部分（下一步要改的四处）

1. **模型**：`external_tool_servers` 加 `transport`（http/stdio）/`command`/`args`。
   `endpoint_url` 目前非空，stdio 行需要可空或存占位 URI——这是个语义决策，
   值得单独设计（占位 URI 会污染所有展示 URL 的地方）。
2. **校验发现**：`validate_server` 现在先 `mcp_security.validate_endpoint(url)`；
   stdio 分支要跳过 URL 校验、改为启动进程做握手 + `tools/list`。
3. **executor 工厂**：`build_external_executors` 里写死了 `MCPClient(endpoint)`；
   需要按 transport 分叉，并决定**进程生命周期**（每次调用启一次 vs 常驻池）。
   每次启动简单但慢（npx 冷启动数秒）；常驻池要处理崩溃重建与并发复用。
4. **前端**：server 创建表单加 transport 选择与命令/参数输入，并给出白名单提示。

第 3 点的「每次启动 vs 常驻池」是真正需要设计决策的地方——它决定并发下的行为与
失败模式。这也是本轮没有仓促接线的核心理由。
