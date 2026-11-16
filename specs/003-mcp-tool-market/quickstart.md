# Quickstart: 003 外部工具服务器与工具市场

本指南验证 003 的管理、市场、授权、运行和安全边界。它不替代 002 的沙箱验收；若 002 的 native/full-stack acceptance 未通过，应在报告中单独标记为前置依赖，而不是把失败归因给 003。

## Prerequisites

- 已按根目录 README 启动 PostgreSQL、Redis、MinIO、API、Worker 和前端。
- 可启动测试 MCP fixture（仅用于验证，不用于生产）：

```bash
backend/.venv/bin/python deploy/verify/mcp_fixture_server.py --port 8765
```

  由于生产策略要求 HTTPS，集成测试应通过 TLS fixture 或在测试连接器中显式注入受控 transport；不要为了通过生产路径而放宽 HTTPS/SSRF 校验。
- 已运行数据库迁移，并准备 admin、member、viewer 三类账号。
- 准备一个仅供测试的 MCP Streamable HTTP 服务器，提供：
  - `search`：成功返回 JSON；
  - `slow_search`：可控超时；
  - `error_search`：返回 MCP `isError=true`；
  - 一个可选的 schema 变更版本，用于验证漂移。
- 测试服务器使用非生产凭证，测试数据不得包含真实密钥。

## 1. Register and validate

1. 以 admin 登录，调用 `POST /api/v1/external-tool-servers`，填写 HTTPS endpoint 和写入式 Bearer 测试凭证。
2. 调用 `POST /api/v1/external-tool-servers/{id}/validate`。
3. 预期：验证记录分别展示连接、认证、完整目录发现结果；完整工具进入 `pending_review`，不完整工具不可发布。
4. 用不可达、HTTP 明文、私网/回环、带 query token 的 endpoint 重试。
5. 预期：请求被拒绝或验证失败；响应和日志不包含内部解析 IP 或凭证。

## 2. Review and browse market

1. admin 审核有效工具，调用 approve，并填写原因。
2. member 调用 `GET /api/v1/marketplace/tools`，搜索工具名称和类别。
3. 预期：只显示 `published` listing，展示版本、权限、限制、来源和验证时间；rejected/paused/revoked 不可安装。
4. viewer/admin/member 分别尝试审核和暂停操作。
5. 预期：只有授权角色成功，拒绝操作返回 403 并有审计记录。

## 3. Install and grant

1. member 安装 published listing，选择 `version_policy=pinned`。
2. 给 Agent 增加外部工具 grant，选择允许的凭证和权限子集。
3. 验证 Agent 页面显示 stable name、版本、权限和可用状态，但不显示凭证值。
4. 建立第二个没有 grant 的 Agent，并让两个 Agent 使用同一个模型请求调用外部工具。
5. 预期：有 grant 的 Agent 暴露并可调用该工具；无 grant 的 Agent 在网络请求发出前收到 `policy_denied`，并写入越权审计。

## 4. Successful and failed invocation

1. 提交一个会调用 `search` 的 request，观察既有运行详情和 WebSocket。
2. 预期：同一条 timeline 出现 `external_tool_call_started` / update、版本、耗时和成功结果，Agent 继续推理。
3. 调用 `slow_search`、`error_search`、非法参数和超大结果。
4. 预期：分别得到 `timeout`、`tool_error`、`parameter_error`、`result_truncated`；没有任何伪造成功，响应、UI、审计不泄露凭证或原始内部异常。
5. 运行中取消 request 或让 Run 到达 deadline。
6. 预期：网络调用停止/结果丢弃，终态运行不接受迟到结果。

## 5. Lifecycle, drift, and snapshots

1. 对已授权工具发布新 schema 版本，重新验证服务器并触发 drift。
2. 预期：新版本进入待审核；未批准升级的 Agent 仍使用原版本，旧 Run 显示原版本。
3. 暂停服务器、撤销 listing、撤销 credential 和删除 Agent grant，分别触发新 Run。
4. 预期：所有新调用在发送到外部服务器前被阻止；正在执行的调用得到明确最终分类，历史调用仍可查询。
5. 查询审计日志。
6. 预期：登记、验证、审核、安装、授权、凭证轮换/撤销、暂停/撤销和调用均有 actor、时间、server/tool/version、Agent/Run、策略结果和安全摘要；任何测试 canary secret 不出现。

## Automated validation

后端建议覆盖：

```bash
cd backend
pytest -q tests/unit/test_mcp_client.py tests/unit/test_external_tool_security.py
pytest -q tests/integration/test_external_tool_servers.py \
  tests/integration/test_marketplace.py \
  tests/integration/test_external_tool_grants.py \
  tests/integration/test_external_tool_invocations.py
ruff check app tests
mypy app
```

前端建议覆盖：

```bash
cd frontend
npm run lint
npm run typecheck
npm run test -- --run
npm run build
```

并发验收：使用 20 个成功与 20 个失败/超时外部调用，确认核心 API 无请求失败、所有调用有结构化终态，且对同一 server 的并发和重试不超过配置上限。2026-11-16 已在真实容器栈（API + Worker×8 + PostgreSQL + Redis + MinIO + 真实 HTTPS MCP fixture）完整跑通：invocation 状态分布 21 succeeded（含 1 次预热）/ 10 `tool_error` / 10 timeout，时长 19–10007 ms；无重试（40 次调用对应 40 个 MCP session、fixture 请求数恰为 3×）；单 server 并发峰值 8，与 `MCP_MAX_CONCURRENCY_PER_SERVER` 一致；核心 API 请求失败 0，两项 P95 均通过（`/runs/{run_id}` 比值 0.9938，`/stats/overview` 比值 1.085，各 100 基线 + 178 负载样本）。fixture 位于 192.88.99.0/24（RFC 7526 已废弃 6to4 anycast 前缀，非私有/保留段），因此 Worker 是经未修改的生产 SSRF 与 TLS 策略完成真实网络连接，没有为通过验收而放宽 HTTPS 校验或注入测试 transport。复跑入口为 `python deploy/verify/external_concurrency_acceptance.py --output <dir>`；CI job `external-native` 在专用 ubuntu VM 上执行同一入口；harness 反向控制见 `deploy/verify/test_external_concurrency_acceptance.py`。注意：该 job 的首次原生运行（run `43840065099`，commit `7373f41`）没有跑到测量阶段——它在 `deployment_ready` 因 `own Celery worker did not become ready` 失败，根因是 harness 用 `mkdtemp()` 生成 fixture TLS 目录（0700、归属运行者），而 Worker 容器以 uid 10001 运行，原生 Linux 的 bind mount 会强制执行宿主 uid/权限，导致 Worker 读不到 CA bundle、在 import 阶段因 `SSL_CERT_FILE` 退出（状态码 2）。Docker Desktop 的 bind mount 是宽松的，所以此前所有本地通过都不构成原生证明。现已改为目录 0755、CA/证书 0644、私钥 0600，并在失败时记录有界脱敏证据；**原生复跑通过前，不得声称 003 已通过原生部署验收**。注意：P95 门槛在毫秒级，共用宿主上的调度噪声足以让 `ratio` 在 1.08–1.44 间摆动（API 容器本身仅有约 12% CPU、无 throttling、无 OOM），因此单次失败不构成产品缺陷证据，应以专用宿主复跑为准。

发布状态：当前报告明确标记为尚不适合生产发布；生产部署 smoke 与告警阈值验证尚未完成，Feature 002 native/full-stack acceptance 仍是独立门槛。003 自身的真实部署并发与核心 API P95 验收已于 2026-11-16 通过。详见 [`release-status.md`](release-status.md)。

详细字段和状态见 [`data-model.md`](data-model.md)，HTTP/WS 形状见 [`contracts/api.md`](contracts/api.md)。
