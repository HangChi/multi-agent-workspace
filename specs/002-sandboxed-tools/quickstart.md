# Quickstart: 沙箱代码执行与工具授权（Sandboxed Tools）

**用途**：本特性的端到端验证指南。实现细节见 `plan.md` / `data-model.md` / `contracts/api.md`，此处只写「怎么跑、看什么、算不算过」。

## 前置条件

- 仓库已部署过 001：`deploy/.env` 存在且 `JWT_SECRET`/`INVITE_CODE` 已改。
- 宿主 Docker 可创建网络与容器；Worker 容器已挂载 `/var/run/docker.sock`。
- 沙箱镜像已构建：`aw-sandbox-python:3.12`（见下方命令）。
- 数据库已迁移到本特性最新版本（含 `tools` seed 与工具名迁移）。

```bash
# 0. 构建沙箱与网络辅助镜像（改 Dockerfile 后需重建）
docker build -f deploy/sandbox/Dockerfile -t aw-sandbox-python:3.12 deploy/sandbox
docker build -t aw-sbx-net:1 deploy/sbx-net

# 1. 起全栈（Mock 网关用于离线复现；有真实 LLM 密钥则去掉 --profile mock）
docker compose -p aw -f deploy/docker-compose.yml --profile mock up -d --build

# 2. 迁移（注意：必须 build migrate，否则镜像里没有新迁移文件）
docker compose -p aw -f deploy/docker-compose.yml build migrate
docker compose -p aw -f deploy/docker-compose.yml run --rm migrate

# 3. 确认 Worker 能看到 Docker
docker exec aw-worker python -c 'import docker; print(docker.from_env().version()["Version"])'
```

> 项目名必须是 `aw`；`.env` 必须在 `deploy/` 下。旧坑：只 build api/worker 不 build migrate，迁移不会生效。

## 验证场景

### S1 隔离环境执行脚本（US1 / FR-006~014 / SC-001, SC-003, SC-008）

1. 建 Agent「计算员」，提示词「你是计算助手，需要计算时调用 run_python」，只勾 `run_python`。
2. 提交需求：「用 Python 计算 1 到 100 的平方和，输出结果」。
3. 拖到该 Agent 执行。

**通过标准**
- 运行详情出现工具步骤，点开可看到脚本源码、`stdout`、`exit_code=0`、耗时；
- 最终答案包含 338350；
- 从调用到结果回传 ≤5 秒（SC-001，不含 LLM 推理时间）；
- WS 依次收到 `sandbox_started` → `sandbox_output` → `sandbox_finished`。

**失败路径**：把需求改成「故意抛异常」，验证 `status=failed`、`error_category=exit_code`、`exit_code=1`、`stderr` 可见，且 Agent 能在下一轮据此修正（SC-008）。

### S2 超时与资源限制（US1 / FR-009~010 / SC-003）

| 用例 | 脚本 | 期望 |
|---|---|---|
| 超时 | `while True: pass`（`timeout_s=5`） | `status=timeout`，5 秒 + ≤3 秒内被杀，容器与网络无残留 |
| 内存 | `x = bytearray(2*1024**3)`（配额 512MB） | `status=resource`，`error_category=oom`，Worker 存活 |
| 进程数 | fork 循环 | `status=resource`，`error_category=pids` |
| 磁盘 | 循环写 1GB 文件 | 有界 workspace/output/tmp 共享 tmpfs 在配额处返回 `ENOSPC`；执行不会越界，产物仍可从 keeper 回收 |

**附加检查（必须执行）**：`docker ps -a | grep aw-sbx` 应为空；`docker network ls | grep aw-sbx` 应为空。

### S3 工具授权真的生效（US2 / FR-001~005 / SC-005, SC-006）

1. `GET /tools` 返回 5 个工具，逐个用「只勾它」的 Agent 跑一次 → 5 个都真实可用（无「选了等于没选」）。
2. Agent 只勾 `calculate`，用 Mock 网关强制模型调用 `run_shell`：
   - 期望：step output 为 `{"ok":false,"denied":true,...}`；WS 收到 `tool_denied`；`audit_logs` 出现一条 `tool_denied`。
3. Agent 不勾任何工具（`tools=[]`）：运行后 `executions` 为空，无任何工具调用成功。
4. 迁移校验（存量数据）：
   ```sql
   -- 迁移后不应再有旧名
   SELECT count(*) FROM agents WHERE tools::jsonb ?| array['web_fetch','shell','http','code_interpreter'];
   -- 期望 0
   SELECT count(*) FROM agents WHERE tools::jsonb ?| array['read_url','run_shell','run_python'];
   -- 期望 = 迁移前勾选了对应旧名的 Agent 数（迁移日志里核对）
   ```
5. 历史快照不被篡改：取一条 001 时期的运行，`steps.tool_name` 仍是旧名（如 `web_fetch`）。

### S4 产出文件成为制品（US3 / FR-015~018 / SC-007）

1. 需求：「用 pandas 生成 20 行销售数据，存成 sales.csv」。
2. 期望：运行详情出现 `sales.csv`，可下载；下载后 `sha256` 与接口返回的校验值一致；行数 20。
3. 越权：用另一个账号 `GET /runs/{id}/artifacts/{aid}` → 404（当前实现隐藏非归属资源的存在；不是 403）。
4. 上限：脚本写 60 个文件 → 只保留前 50 个且提示截断；写单个 30MB 文件 → 截断或拒绝并提示。

### S5 出站：默认放行、黑名单强制、限制模式（US5 / FR-019~023 / SC-009, SC-010）

| 用例 | 脚本 | 期望 |
|---|---|---|
| 默认放行 | `pip install requests` 或 `curl -sS https://pypi.org` | 成功；`egress_accesses` 出现 `pypi.org:443 allow` |
| 元数据拦截 | `curl http://169.254.169.254/latest/meta-data/` | 失败且原因可读（策略拦截）；记录 `deny` + `matched_rule_id` |
| 直连 IP | 直接连某公网 IP:443 | 记录 `target_domain=ip-direct`，`action=allow` |
| 内网拦截 | 连 `10.0.0.4:5432` | `deny` |
| 限制模式（IP/CIDR/域名） | Agent 设为 `restricted`，只加目标 IP/CIDR 或精确/通配域名及端口；访问白名单目标后再访问未授权目标 | 白名单目标成功，未授权目标拒绝且留痕；域名答复前必须完成 TTL/IP 族/端口内核绑定 |
| 成员不能绕过 | 普通成员调用 `DELETE /admin/egress-blacklist/{内置项}` | 403；内置项本身不可删（400） |

> **域名与审计边界**：有域名规则时使用执行私有可信 DNS mediator；完整 CNAME 链、全局 deny 优先、TTL 过期及 helper 健康失效均需实测，失败时 fail-closed。共享 dnsmasq 日志不授权。策略约束目的 IP+端口，不识别 TLS/SNI/HTTP Host 身份，不能推断未观察的共享 IP 别名。内置 deny 的 `matched_rule_id` 为 null（非数据库规则行）；`bytes_out` 为 null（不采集 payload）。专项 VM 结果不替代原生整栈验收。

```bash
# 核对本次执行的包头连接/流事实与拒绝事实；不能只看总数或事后 conntrack
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8080/api/v1/runs/$RUN/executions/$EXEC/egress | jq '.items | length'
```

### S6 可观测、配额与并发（US4 / FR-024~028 / SC-004）

1. 连续触发 20 个含 `run_python` 的需求（脚本 `time.sleep(3)`）。
2. 期望：并发不超过 `SBX_MAX_CONCURRENCY`（默认 4），其余排队而非报错；期间 `GET /api/v1/stats/overview` 的 P95 劣化 ≤20%（SC-004）。
3. 把并发上限调成 1 再打 5 个，验证「沙箱繁忙」提示出现在 step output 与运行详情，`error_category=resource`。
4. 失败分类齐全性：跑一遍 S2 + S5 的失败用例，确认五类（`timeout/oom/pids/disk/infra` + `exit_code` + `policy`）都能在详情页看到中文可读文案，无「未知错误」（SC-008）。
5. 审计：`GET /api/v1/audit-logs?action=sandbox_exec` 覆盖每次执行；`action=egress_denied` 覆盖每次拦截。

## 验收对照（SC → 验证方式）

| SC | 验证场景 | 判定 |
|---|---|---|
| SC-001 简单脚本 ≤5s | S1 | 计时 |
| SC-002 10 类越权 0 逃逸 | S2 + S5 + 专项脚本 | 见下「越权矩阵」 |
| SC-003 超时 3s 内终止且无残留 | S2 超时用例 | `docker ps -a` 空 |
| SC-004 20 并发 P95 劣化 ≤20% | S6 | 压测前后对比 |
| SC-005 越权调用 100% 拒绝留痕 | S3-2 | 审计计数 |
| SC-006 勾选与可用工具一致 | S3-1/3 | 5 个 Agent 对照 |
| SC-007 制品一致率 100% | S4 | sha256 比对 |
| SC-008 失败分类可读 | S2 + S5 + S6-4 | 无「未知错误」 |
| SC-009 出站记录覆盖率 100% | S5 | 计数比对 |
| SC-010 黑名单拦截率 100% | S5 | 全部 deny 命中 |

### 越权矩阵（SC-002，逐项必须为「失败且不可见」）

| # | 尝试 | 期望 |
|---|---|---|
| 1 | 读 `/etc/passwd` 之外的宿主路径（如 `/host`） | 不存在 |
| 2 | 读环境变量里的 `DATABASE_URL`/`JWT_SECRET` | 不存在（沙箱只注入白名单环境变量） |
| 3 | 访问 `169.254.169.254` | 拦截 |
| 4 | 访问 `10.0.0.0/8` 内任意地址 | 拦截 |
| 5 | fork 炸弹 | pids 超限被杀 |
| 6 | 内存耗尽 | oom 被杀 |
| 7 | 写满磁盘 | 配额内失败 |
| 8 | 路径穿越写 `../../` | 限制在 `/workspace` |
| 9 | 尝试 `sudo` / 提权 | 非 root，无权限 |
| 10 | 后台残留进程（`nohup` / `disown`） | 执行结束整体回收 |

## 验证记录表

| 场景 | 状态 | 记录 |
|---|---|---|
| S1 脚本执行 | ⏳ | 原生 Linux run `43788760783` 的基础迁移、I/O 2/2、越权矩阵 13/13 和执行清理通过；完整验收仍因后续 HTTP 功能/性能门禁失败，不能单独标记闭环通过 |
| S2 超时与资源 | ⏳ | 原生 run `43788760783` 的受控 I/O、escape/资源相关场景通过；整体 SC-002/SC-003 仍须在所有强制 gate 通过后确认，VM 结果不替代原生结论 |
| S3 工具授权 | ⏳ | 原生 run `43788760783` 迁移/历史与 HTTP 基础场景通过，但完整授权链路随 HTTP functional 13/14 失败保持待验收 |
| S4 制品 | ⏳ | 既有 VM 实测沙箱→MinIO→HTTP GET 的 20 行/hash 一致；原生 run `43788760783` 未形成可将 API 制品/归属/上限单独判为完成的证据 |
| S5 出站策略 | ⏳ | 原生 run `43790753355` 域名矩阵 18/18、IP/多连接/IPv6 通过，但公网 HTTPS 有一次超时且 HTTP `egress_api` 仍失败；前置 resolver 20/20、后验 A/AAAA 40/40 均 answered，不能替代强制用例 |
| S6 配额并发 | ⏳ | 原生 run `43790753355` 20/20 执行成功、实际峰值 4；API P95 基线/负载 4.766/11.692ms，比值 2.4531 > 1.2，quota 通过但严格门槛仍失败，故保持 ⏳ |

> 状态只允许在真实容器栈上跑通后改成 ✅，并附 commit 与关键输出摘要；未跑过一律留 ⏳。

## 复现脚本

2026-11-15 证据、适用边界与未完成项见 [Docker 验证记录](docker-verification.md)。测试环境是 Docker Desktop Linux VM，不冒充原生 Linux 云主机验收。`deploy/docker-compose.verify.yml` 使用独立项目 `aw-002-verify`，不加载开发 `.env`、不共用开发数据库。

```bash
# 从仓库根目录；后端回归必须指向专用测试服务。
# tests/conftest.py 会重建测试 schema，绝不能指向开发/生产库。
cd backend && DATABASE_URL="postgresql+asyncpg://aw:<测试密码>@127.0.0.1:55432/agent_workbench_test" \
  REDIS_URL=redis://127.0.0.1:56379/0 .venv/bin/python -m pytest -q

# 以下从仓库根目录执行。API 验收需预先提供有效 owner/other/admin token；
# 或在获批后用 SANDBOX_VERIFY_INVITE_CODE 创建成员。缺少身份时不伪造通过。
backend/.venv/bin/python deploy/verify/s3_sandbox_scenarios.py \
  --base http://localhost:8180 --artifact-fetch-container aw-002-verify-api-1

# 下列 runtime 探针不访问应用数据库，不替代受保护 API 验收。
EGRESS_DNS_PROXY=aw-002-verify-dns SBX_NETWORK_PREFIX=aw-002-probe \
  backend/.venv/bin/python deploy/verify/s5_egress_scenarios.py \
  --public-url https://pypi.org --output /tmp/aw-002-egress.json
EGRESS_DNS_PROXY=aw-002-verify-dns SBX_NETWORK_PREFIX=aw-002-probe \
  backend/.venv/bin/python deploy/verify/sbx_escape_matrix.py \
  --output /tmp/aw-002-matrix.json
docker exec -i aw-002-verify-worker-1 python - < deploy/verify/s4_artifact_runtime.py
```

最新 `sbx_escape_matrix.py` 在 Docker Desktop Linux VM 上 13/13 通过，包含有界 output 内核配额探针；不会为验证而写满宿主磁盘。最新出网探针 11/11 覆盖 IP/CIDR、多 TCP 连接和 IPv6 fixture，不含可选 public HTTPS；此前版本的 HTTPS 证据不替代当前版本重跑。它们均不证明域名规则、权限 API、受保护整链路或 100% 访问覆盖。

### 原生 Linux CI（强制完整验收）

`.github/workflows/ci.yml` 在全新 GitHub Ubuntu VM 上运行 `native_acceptance.py`，仅该入口负责创建随机凭据和全新独立验收数据库；本地执行会被拒绝。它不加载 `deploy/.env`，不使用本地既有身份，不重启开发服务，只回收本次 project/UUID 资源。

SC-004 已自动化，不能推迟到 P3：同时提交 20 个固定 `time.sleep(3)` 工作负载，独立采样实际活容器须观察并行且不超过 4；先收集 100 个 baseline，再保留全部 load 样本，以 nearest-rank P95 比较，`load/base ≤1.2`，请求与运行均须零失败。不重试测量请求，不剔除异常值，不改变工作负载，不用缩短进程耗时冒充 SC-001 完整执行窗口。CI 仅上传有界脱敏 JSON，不上传原始服务日志、环境变量、凭据或预签名 URL。
