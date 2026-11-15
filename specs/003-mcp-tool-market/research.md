# Research: 外部工具服务器与工具市场

**Feature**: 003-mcp-tool-market  
**Date**: 2026-11-15

## Decision 1: v1 只支持 Streamable HTTP

- **Decision**: 外部 MCP 服务器 v1 使用 Streamable HTTP；不支持 stdio、已废弃的 HTTP+SSE 或自定义传输。
- **Rationale**: 当前系统由独立 API 与 Celery Worker 组成，HTTP 服务器可由 Worker 直接访问，不需要在 Worker 中启动和回收管理员提供的任意子进程。这样避免把第三方命令执行引入 Worker 进程边界，同时保留 MCP 的初始化、能力协商、`tools/list` 和 `tools/call` 语义。
- **Alternatives considered**:
  - stdio：生态兼容性好，但需要执行任意服务器命令、隔离子进程、注入环境变量并处理僵尸进程和密钥泄露，超出当前安全边界。
  - HTTP+SSE：协议已迁移到 Streamable HTTP，v1 不新增兼容层。

## Decision 2: 使用现有 httpx 建立受限异步 MCP 适配器

- **Decision**: 在 Worker 侧以 `httpx` 实现受限的 JSON-RPC/MCP 客户端适配器，并实现现有 `ToolExecutor` 适配接口；如后续官方 SDK 的异步 Streamable HTTP 能力稳定且不削弱边界控制，再评估替换。
- **Rationale**: 项目已依赖 `httpx`；直接适配器能在单一边界集中实施 HTTPS、连接/读取/响应大小限制、重定向禁止、参数校验、取消和错误分类，避免把不透明的远端结果直接带入 Runner。
- **Alternatives considered**:
  - 官方 Python SDK：可减少协议漂移，但当前计划要求精确控制 SSRF、响应上限、取消与重试；先封装内部协议边界，再保留替换点。
  - 为每种服务器实现专用插件：扩展成本高且会破坏市场统一治理。

## Decision 3: 外部工具定义与内置 CATALOG 分离

- **Decision**: 外部工具使用数据库中的服务器、发现版本、发布、安装和授权实体；运行时解析为与内置工具相同的模型工具 schema，并通过注入的 executor 映射进入现有 ReActRunner。不得将远端可变定义写入静态 `CATALOG`。
- **Rationale**: 002 的 `CATALOG` 是内置实现的单一事实源，且现有 `agents.tools` 和 `Run.tool_grants_snapshot` 只保存名称。外部能力需要来源、版本、权限、凭证引用和 schema 快照，必须独立建模并在入队时冻结。
- **Alternatives considered**:
  - 直接扩展 `CATALOG`：会把动态、可撤销的远端定义误当成内置能力，且无法表示版本/服务器身份。
  - 只把外部工具名称追加到 `agents.tools`：无法保证安装、发布状态、版本和凭证作用域，也会削弱 002 的 fail-closed 逻辑。

## Decision 4: 版本和授权在 Run 入队时冻结

- **Decision**: 安装、授权、服务器、工具版本、权限范围、参数 schema 摘要和凭证引用元数据在 Run 创建/分配时生成不可变快照；执行时再次检查当前状态，只允许快照仍满足当前发布、安装、授权、凭证和配额条件的调用。
- **Rationale**: 既要保证历史运行可解释，又要让暂停、撤销和凭证撤销对新调用立即生效。运行时采用“快照确定身份 + 当前状态 fail-closed”交集。
- **Alternatives considered**:
  - 每次调用读取最新工具定义：历史不可重放，版本漂移会改变已有运行语义。
  - 只相信快照：暂停或撤销不能立即阻止新调用，违反安全和治理要求。

## Decision 5: 凭证只支持服务器级静态密钥，OAuth 延后

- **Decision**: v1 支持服务器范围的静态 Bearer/API-Key header，使用新的加密存储服务；不实现用户级 OAuth 授权流程，不把 token 放进 URL。
- **Rationale**: 项目已有 JWT 与日志脱敏，但没有通用的 Fernet 凭证库。v1 可在清晰的密钥管理边界内完成服务器级接入；OAuth 2.1、PKCE、资源指示器和用户同意会显著扩展身份、回调和撤销范围。
- **Alternatives considered**:
  - 直接明文存储：违反宪法，不可接受。
  - v1 同时支持 OAuth：会把市场接入、用户授权和 token 生命周期混成一个高风险阶段，延期到独立特性。

## Decision 6: 外部服务器地址按 SSRF 高风险目标处理

- **Decision**: 登记和每次连接均执行 HTTPS、目标地址解析和私有/回环/链路本地/保留地址拒绝；禁用重定向或逐跳重新校验；限制端口和响应体，连接失败按策略拒绝记录。
- **Rationale**: 002 的出网规则只约束沙箱网络，Worker 的 httpx 请求不会自动经过沙箱防火墙。即使服务器由管理员登记，也不能让一个错误或被接管的地址访问内部服务、云元数据或控制面。
- **Alternatives considered**:
  - 仅登记时校验：DNS rebinding、重定向和地址变化仍可绕过。
  - 仅依赖部署网络：无法形成可测试、可审计的应用层安全边界。

## Decision 7: 不可信结果先限额、再作为数据回填

- **Decision**: 将服务器的工具描述、错误、文本、结构化结果和资源链接视为不可信数据；只接受受支持的文本/JSON 内容，执行大小和结构限制，必要时截断。结果不得修改系统提示词、授权、凭证、审计或管理状态。
- **Rationale**: 远端工具可以返回提示注入、恶意指令或巨大内容。Runner 当前会把工具结果直接写入 tool message，因此必须在 executor 与持久化边界前做清洗和限额，并为 UI 使用转义后的投影。
- **Alternatives considered**:
  - 原样转发：会造成提示注入、日志/存储膨胀和敏感值泄露。
  - 完全丢弃结果：无法实现工具价值；限额并标记为不可信可保留可用性。

## Decision 8: 失败分类和取消由统一运行链路承载

- **Decision**: 外部调用实现连接失败、认证失败、参数错误、协议错误、工具 `isError`、超时、策略拒绝、结果超限等结构化类别；复用 Runner 的 step/event/WS/审计链路。调用前校验，调用中绑定剩余 Run deadline，取消时中止 HTTP 请求并忽略迟到结果。
- **Rationale**: 不增加第二套执行视图；用户必须在既有运行详情中理解外部调用。现有 Runner 对普通 executor 异常默认为 completed，需要 003 明确补充外部失败投影和测试。
- **Alternatives considered**:
  - API 轮询独立任务：会绕过现有状态机和实时日志。
  - 把所有错误转成普通文本：模型和用户无法区分策略拦截、外部故障与工具业务错误。

## Existing implementation caveat

002 的代码提供了可复用的 catalog、授权、Runner、审计和沙箱接口，但当前 native Linux/full-stack acceptance 尚未通过，且现有凭证库不存在。003 的设计只消费这些接口并为外部能力增加独立模型/适配器；不会把 002 未完成项伪装成 003 的交付内容。
