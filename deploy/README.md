# 部署（deploy/）

本目录提供「一条命令起全套」的 Docker Compose 编排，以及真实栈验证脚本。

## 服务清单

| 服务 | 容器名 | 端口 | 说明 |
|---|---|---|---|
| frontend | aw-frontend | `${FRONTEND_PORT:-8080}` | nginx 托管 SPA，反代 `/api` 与 `/ws` 到 api |
| api | aw-api | 仅容器内 8000 | FastAPI（uvicorn 2 worker），不暴露到宿主机 |
| worker | aw-worker | - | Celery Worker，执行 Run（`WORKER_CONCURRENCY`，默认 2） |
| beat | aw-beat | - | Celery Beat，周期任务调度（含每分钟的调度扫描） |
| migrate | aw-migrate | - | 一次性：`alembic upgrade head`，api/worker/beat 等它成功 |
| postgres | aw-postgres | `127.0.0.1:5432` | PostgreSQL 16，仅本机可连 |
| redis | aw-redis | `127.0.0.1:6379` | 队列 / 事件 Pub-Sub / 结果后端 |
| minio | aw-minio | `127.0.0.1:9000` / `9001` | S3 兼容对象存储（附件与制品） |
| prometheus | aw-prometheus | `127.0.0.1:9090` | 指标抓取 + 8 条告警规则（`--profile observability`） |
| grafana | aw-grafana | `127.0.0.1:3000` | 总览看板（`--profile observability`，provisioning 自动加载） |
| llm-gateway | aw-llm-gateway | 仅容器内 4000 | **仅验证用** Mock LLM，`--profile mock` 才启动 |

## 启动

```bash
cp deploy/.env.example deploy/.env     # 按需修改密钥（生产必改）
docker compose -p aw -f deploy/docker-compose.yml up -d --build
```

> 项目名固定为 `aw`（容器名 aw-*）。不加 `-p aw` 时 compose 会按目录名推导项目名，
> 与已有容器冲突（`container name already in use`）。

### 两种进程拓扑：多容器（默认）/ 单容器

| 模式 | 容器 | 适用 | 代价 |
|---|---|---|---|
| 多容器（默认） | api / worker / beat 各自独立 | 多用户、要压测、要单独扩 worker | 三者各自构建重启，容易出现「api 已是新代码、worker 还是几小时前的镜像」 |
| 单容器 | `app`（uvicorn + celery worker + beat 同进程组） | 单机自用、演示、少运维 | ① 统一 `DB_POOL=null`，API 失去连接池；② 不能单独扩 worker（scale 会连带复制 beat，同一 cron 触发两次） |

```bash
deploy/single.sh     # 切单容器（自动停 api/worker/beat，起 app + frontend）
deploy/multi.sh      # 切回多容器（自动移除 aw-app）
deploy/single.sh status   # 看当前是哪种
```

### 后端只有一个镜像（aw-api）

api / worker / beat / migrate / app 五个服务**共用 `image: aw-api`**，只有 api
服务带 `build:`。此前每个服务各自 build，宿主机上会留下 5 个内容几乎相同的
881MB 镜像，改一行代码要重复构建 5 次，还容易出现「worker 忘了重建」的版本
漂移。现在只有一份，切模式不需要重建。

部署脚本已同步：`deploy.sh` 显式 `compose build api`（其余服务没有 build 段，
只写 `$SERVICES` 时若不含 api 会「No services to build」，随后 up 用旧镜像
静默起服务）；`single.sh` 同样先 build api。

> ⚠️ 构建需要能访问 PyPI。在受限沙箱里 `uv sync` 会以
> `failed to lookup address information` 失败——此时只能复用本地已有镜像
> （`docker tag` 到 aw-api），不能重建。

单容器的实现要点：三个进程共享**同一个镜像**，版本漂移在物理上不可能；
`wait -n` 保证任一进程退出就整容器退出（交给 `restart: unless-stopped`），
不存在「半个系统还活着」。`app` 在 compose 网络里挂了别名 `api`，所以
nginx 的上游 `http://api:8000` 两种模式通用，不用改前端镜像。

本地离线验证（无真实 LLM 密钥时）：

```bash
docker compose -p aw -f deploy/docker-compose.yml --profile mock up -d --build
# 并把 .env 的 LLM_BASE_URL 指向 http://llm-gateway:4000
```

### 前端三种启动方式

前端的 API 请求走相对路径（`src/api/client.ts` 的 `baseURL: '/api/v1'`），
所以它总是"从当前页面的 host"取接口，三种方式都不需要配 baseURL：

| 方式 | 命令 | 地址 | 适用 |
|---|---|---|---|
| 容器（推荐） | `docker compose -p aw -f deploy/docker-compose.yml up -d frontend` | http://127.0.0.1:8080 | 日常使用 / 验收，与生产同构 |
| 开发服务器 | `cd frontend && npm run dev` | http://127.0.0.1:5173 | 改前端代码，热更新 |
| 静态预览 | `cd frontend && npm run build && npm run preview` | http://127.0.0.1:4173 | 验证**构建产物**（而非源码），无热更新 |

（三者都实测过：`vite preview` 会复用 `server.proxy` 配置，所以 4173 下
`/api/v1/health` 同样返回真实 JSON，不是 SPA fallback。）

**开发服务器依赖后端已经在跑**。它把 `/api` 与 `/ws` 代理到 `AW_DEV_API`
（默认 `http://127.0.0.1:8080`，即 nginx 入口）：

```bash
# 1) 起后端（api/worker/beat + nginx）——只需一次
docker compose -p aw -f deploy/docker-compose.yml up -d --build

# 2) 起前端热更新
cd frontend && npm run dev          # → http://127.0.0.1:5173

# 若后端是用 uvicorn 直接起在宿主机（不经 compose），覆盖代理目标：
cd frontend && AW_DEV_API=http://127.0.0.1:8000 npm run dev
```

> ⚠️ 不要在 `deploy/.env` 里给 `api` 加 `ports: 8000:8000` 来"让 dev 能代理"。
> 容器里该服务刻意只 `expose` 不 `publish`（见 compose 该服务末尾注释），
> 后端只该经 nginx 暴露——把 8000 放出来等于凭空多一个绕过 nginx
> 安全头（CSP / X-Frame-Options）的入口。

## 受限网络下的构建（走代理）

有公网的机器直接 `up -d --build` 即可。若构建机只能通过代理出网，`uv sync` /
`npm ci` 会在**容器内**报 DNS 解析失败：

```
uv sync ... failed to lookup address information: Name does not resolve
```

此时把宿主代理传给 buildkit（`HTTP_PROXY` / `HTTPS_PROXY` 是 Docker 的**预定义
build-arg**，不需要在 Dockerfile 里声明）：

```bash
# 后端（compose 里 api/worker/beat 共用同一个镜像 aw-api）
docker build --build-arg HTTP_PROXY=http://host.docker.internal:7897 \
             --build-arg HTTPS_PROXY=http://host.docker.internal:7897 \
             -t aw-api:latest backend

# 前端
docker build --build-arg HTTP_PROXY=http://host.docker.internal:7897 \
             --build-arg HTTPS_PROXY=http://host.docker.internal:7897 \
             -t aw-frontend:latest frontend

# 镜像变了，compose up 会自动重建容器（不需要再 --build）
docker compose -p aw -f deploy/docker-compose.yml up -d api worker beat frontend
```

三个前提：

1. **代理要能被 Docker VM 访问**。只绑 `127.0.0.1` 的代理在 macOS 上通常可以通过
   `host.docker.internal` 访问（Docker Desktop 的 vpnkit 转发）；Linux 上需要让代理
   监听网桥地址（如 `0.0.0.0:7897`）或改用 `--network=host` 构建。
2. **`docker compose build` 不接受 `--build-arg`**，所以用 `docker build` 直接产出
   同名镜像（`aw-api:latest` / `aw-frontend:latest`），compose 只负责起容器。
3. 构建 args 不进镜像层历史，也不该写进 `.env`（那是运行时配置，两者不是一回事）。

实测（2026-11-21，macOS + Docker Desktop，宿主 mihomo 代理 127.0.0.1:7897）：
后端 `uv sync` 装 102 包 31.8s、前端全量构建 38s，两者一次通过；
构建出的镜像上 `s14` 5/5、`e2e` 19/19 全绿。

## 接一个 LLM 端点

三个变量，都在 `deploy/.env`：

| 变量 | 说明 |
|---|---|
| `LLM_BASE_URL` | OpenAI 兼容端点，**带 `/v1`**（如 `https://your-gateway/v1`） |
| `LLM_API_KEY` | 端点密钥 |
| `LLM_MODEL` | 兜底模型名，**不要写 `openai/` 前缀** |

- 真正决定调用哪个模型的是 **`Agent.model`**（每个 Agent 自己选）；`LLM_MODEL` 只在
  没有 Agent 语境时兜底。
- LiteLLM 必须靠模型前缀选 provider，本项目在 `app/executors/llm.py::litellm_model_name()`
  里统一补 `openai/`（仅当 `LLM_BASE_URL` 非空）。所以数据库、成本表、指标标签里的
  模型名保持干净，**不要自己写前缀**。
- ⚠️ **换端点必须迁移存量 Agent 的 model**。旧值是上一个端点的模型名（如 `gpt-4o-mini`），
  新端点没有它，run 会**秒失败**并报 `No available channel for model gpt-4o-mini`：

  ```sql
  -- 只改新端点确实不存在的那些，不要全表覆盖
  UPDATE agents SET model='<新端点模型名>'
   WHERE model IN ('gpt-4o-mini','gpt-4o','claude-3-5-sonnet');
  ```

- 前端模型下拉是硬编码列表（`frontend/src/components/agent/AgentForm.tsx` 的 `MODELS`），
  换端点要同步改；不在列表里的存量值会显示成「（不在预设列表）」而不是空白。

换完先验端点，再验链路：

```bash
# 1) 端点 + key + 模型名三者是否匹配（宿主机直连）
curl -s "$LLM_BASE_URL/chat/completions" -H "Authorization: Bearer $LLM_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"'"$LLM_MODEL"'","messages":[{"role":"user","content":"hi"}],"max_tokens":16}'
# 2) 整条链路：建 Agent(用同一 model) → 建需求 → 分配，看 Run 是否 completed 且 result 非空
```

## 生产注意

1. **`APP_ENV=production`（默认）会强校验密钥**：`JWT_SECRET` 需 ≥32 字符且非默认值，
   `INVITE_CODE` 需 ≥12 字符且非默认值，否则 api 拒绝启动（防止弱密钥上线）。
2. **禁用 mock 网关**：`llm-gateway` 只在 `--profile mock` 下启动，生产请把
   `LLM_BASE_URL` 指向真实 LiteLLM/OpenAI 兼容网关。带着 mock 跑会返回假结果。
3. **只暴露必要端口**：宿主机只映射 `frontend`（8080）。postgres/redis/minio 绑
   `127.0.0.1`，api 完全不映射——对外请在前面加 HTTPS 反代（见下方 Caddyfile）。
4. **健康检查分两层**：`/api/v1/health` 是存活探针（进程能响应），
   `/api/v1/health/ready` 是就绪探针（DB + Redis 可达），compose 用后者。
5. **资源限制**：每个服务都设了 CPU/内存上限，避免单个失控 Run 拖垮整机。
   按机器规格在 `docker-compose.yml` 的 `deploy.resources.limits` 调整。
6. **日志轮转**：所有服务 `json-file` 单文件 10MB × 3 份，避免磁盘被写满。

## 沙箱镜像与网络运维

Compose 中的沙箱执行镜像由 Worker 启动；网络规则镜像用于执行级固定命令批处理、包头观测，以及需要域名策略时的独立 DNS mediator。共享 DNS 代理长期运行但不向宿主机发布端口，只作为受控上游。三者的镜像 tag 必须与后端配置及 Compose 一致：`aw-sandbox-python:3.12`、`aw-sbx-net:1`、`aw-dns-proxy:1`。

```bash
# 从仓库根目录分别构建（CI 也会逐个构建验证）
docker build -t aw-sandbox-python:3.12 deploy/sandbox
docker build -t aw-sbx-net:1 deploy/sbx-net
docker build -t aw-dns-proxy:1 deploy/dns-proxy

# 更新 Compose 管理的服务（包括 DNS 代理）；沙箱执行/网络镜像需用上面的命令单独重建
docker compose -p aw -f deploy/docker-compose.yml build
docker compose -p aw -f deploy/docker-compose.yml up -d
```

`deploy/deploy.sh` 会在换完应用服务后补一句 `up -d dns-proxy`：**DNS 代理不能漏启动**。
它不是被替换的应用服务，而是沙箱执行的前置依赖；漏了不会让部署失败，只会让
`run_python` / `run_shell` 全部返回 `infra`（`DNS proxy has no address on sandbox network`），
而 Run 依旧以 `completed` 收尾——模型会诚实地说"没能真正执行"，但看板上看着像成功。

沙箱**执行**镜像（`aw-sandbox-python:3.12`）不在 compose 里，也不由 deploy.sh 构建，
必须手工建一次（上面第一条命令）；缺了同样只会让工具报 `infra`：
`沙箱镜像 aw-sandbox-python:3.12 不存在，请先构建镜像`。

Beat 每天 03:00（`Asia/Shanghai`）清空创建时间超过 90 天的沙箱代码、stdout 和 stderr；代码以空字符串保留以满足非空约束。`code_sha256`、状态、资源限额和其他执行元数据不删除。

沙箱的网络隔离和包头观测**要求 Linux Docker 主机**：iptables/ip6tables、nftables、bridge netfilter、AF_PACKET 与 cgroup 必须实际可用。Docker Desktop Linux VM 的专项结果与原生 Linux 验收分开记录。GitHub Actions 使用完整 `ubuntu-24.04` VM 执行 `deploy/verify/native_acceptance.py`，实测这些前置条件并创建独立临时栈；不使用 slim/container job 代替宿主验证。生产上线前仍应在目标 Linux 主机复验，CI 不构成对所有发行版或已攻陷宿主的隔离保证。

### DNS 故障排查

```bash
# 检查代理状态、健康情况与近期 DNS 查询（查询日志包括源 IP）
docker compose -p aw -f deploy/docker-compose.yml ps dns-proxy
docker logs --tail 200 aw-dns-proxy 2>&1 | grep -E 'query\\[|forwarded|reply'

# 检查配置挂载与容器解析器；dns-proxy 不应发布宿主机端口
docker inspect aw-dns-proxy --format '{{json .Mounts}}'
docker inspect aw-dns-proxy --format '{{json .NetworkSettings.Networks}}'
```

确认 `deploy/dns-proxy/blacklist.conf` 中静态规则格式为 `address=/domain/`，且 `dnsmasq.conf` 中指定的上游 DNS 可达。应用管理端的全局规则在下一次执行冻结进策略快照，不需要重启共享代理；只有手工修改共享上游的挂载配置后，才需要使其重新加载：

```bash
docker compose -p aw -f deploy/docker-compose.yml restart dns-proxy
```

需要域名策略时，每次执行独立的可信 mediator 验证原始域名、完整 CNAME 链、地址与端口规则，并在发送 DNS 答案前原子更新执行私有 nft 超时集合。共享 dnsmasq 日志不是授权依据。全局 deny 优先；TTL 过期或健康租约失效后，已建立连接也不能绕过当前规则。mediator 死亡、暂停、绑定失败、不可安全处理的短 TTL/截断答案或预算耗尽均 fail-closed。默认模式存在全局域名 deny 时，未通过该执行受控 DNS 建立安全绑定的直连 IP 同样拒绝。

排障时先查共享上游，再按 `aw.execution` 标签定位本次执行的 mediator/包头 helper，检查就绪、受信心跳和 Worker 错误分类；不要清理其他执行或全局刷新规则。用户容器不持有网络管理能力、Docker socket 或 helper 参数控制权。

### 安全边界与观测限制

- Worker 连接 Docker Engine 需要访问 Docker socket。**可访问该 socket 的进程通常等价于宿主机 root**，能够创建特权容器、挂载宿主文件系统或控制其他容器。当前部署对 Worker 挂载 socket 是高权限信任边界，不是抵御已攻陷 Worker 的隔离措施；应限制主机访问、管理组成员和 Worker 镜像来源，并评估专用主机/受限 Docker API 等隔离方案。不要把 socket 暴露给不可信网络。
- 网络审计只记录 DNS 查询与连接目标等元数据；不解密 TLS，也看不到 HTTPS URL 路径或请求/响应正文。CDN、反向代理和共享 IP 会让 IP 到域名的映射不唯一，域名归因可能缺失或不准确；直接 IP 连接也可能只显示 `ip-direct`。不能将审计结果描述为完整 URL 级追踪。
- 路由流量的片段、IPv6 扩展链及其他不支持的协议在 defrag 之前由执行私有 nft guard 拒绝；记录真实 IP 目标，无法使用 TCP/UDP 端口时 `port=0`。固定 AF_PACKET helper 仅采集包头，并校验执行源 IP、bridge/veth 归属、丢包统计与日志完整性；不能把丢失的审计静默当作成功。正常邻居发现控制流量不作为应用连接记录。
- 域名授权的内核边界仍是「目的 IP + 端口」，不是 HTTP Host/SNI/证书身份检查。已观察的黑名单别名优先阻断共享地址；未查询过的别名不能仅凭 IP 推断。Docker Desktop 专项结果不能代替原生 Linux 和目标部署机验收。

## 外部 MCP 工具（003）

MCP v1 仅允许通过 Worker 访问 Streamable HTTP 服务器；生产 endpoint 必须为 HTTPS，不能在 URL 中携带凭证。外部服务器登记会在应用层校验主机解析结果和端口，拒绝 loopback、私网、link-local、云元数据及重定向目标。该连接器与 002 的沙箱出网规则是不同的边界，不能假设 Worker 的 HTTP 请求自动经过沙箱防火墙。

开发示例 fixture（仅用于测试）：

```bash
backend/.venv/bin/python deploy/verify/mcp_fixture_server.py --host 127.0.0.1 --port 8765 --scenario normal
```

此 fixture 使用本地 HTTP，仅用于协议单测/受控测试 transport；它**不能**作为生产 endpoint，也不能通过降低 HTTPS 或 SSRF 策略来连接。正式集成验证使用测试内注入的 TLS transport/fixture。服务器凭证的开发密钥通过 `MCP_CREDENTIAL_KEY` 配置；生产必须显式替换为独立高熵密钥，并按部署密钥轮换流程管理。

003 的可观测性 API 提供管理员诊断用的计数器和近期告警；当前实现是进程内有界计数，不是持久化或跨 worker 聚合的生产指标。上线前应通过目标部署的 metrics/log collector 验证告警阈值与聚合行为。

当前 003 release gate 状态和已验证结果见 [`specs/003-mcp-tool-market/release-status.md`](../specs/003-mcp-tool-market/release-status.md)。**工程门禁已全部通过**：原生 Linux 上的真实部署并发与核心 API P95 验收、Feature 002 独立验收均已在 2026-11-17 跑通（run `44003410806`）。剩下两项在工程范围之外——**生产环境部署 smoke** 与**告警阈值在真实流量下的校准**，需要有真实部署及其 metrics/log 采集端才能做。

## 定时调度（004）

`aw-beat` 每分钟投递一次 `scan-due-schedules`，`aw-worker` 消费后扫描 `schedules` 中
`enabled AND next_run_at <= now()` 的规则，逐条独立加锁、独立事务——一条出错不影响其余。

必看的几个环境变量（完整清单与取值理由见 `.env.example` 的「定时调度（004）」段）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SCHEDULE_SCAN_ENABLED` | `true` | 总开关。`false` 时 Beat 仍调用任务但立即返回，用于排障时静默全部调度 |
| `SCHEDULE_MISSED_GRACE_SECONDS` | `90` | 错过宽限期。取 0 会把正常的几十秒延迟都记成"错过"，让该判定失效 |
| `SCHEDULE_MAX_FAILURES` | `5` | 连续失败阈值，达到后自动停用规则并写 `auto_disabled_reason` |
| `SCHEDULE_BATCH_SIZE` | `200` | 单次扫描上限，超出留到下一分钟、不丢弃 |
| `SCHEDULE_HISTORY_MAX_ROWS` / `_RETENTION_DAYS` | `500` / `90` | 触发历史保留策略，每日 03:30 清理 |

**运维要点**：

1. **Beat 停了会漏跑，且不补跑。** 停机期间跨过的计划时刻会被合并成一条 `missed`
   记录（`missed_count` 为次数），恢复后只从 `now` 之后重新对齐，**不会**把错过的
   任务一次性补齐。这是刻意的取舍——一次性补跑几十个需求会瞬间打满 worker 与配额。
2. **排查"规则不动了"先看三处**：`aw-beat` 日志里有没有逐分钟的
   `Sending due task scan-due-schedules`；`aw-worker` 有没有 `received` 该任务；
   规则的 `last_result` / `consecutive_failures` / `auto_disabled_reason` 是否已指向具体原因。
3. **容器资源限额未实测**：beat 配额为 0.5 CPU / 256MB，扫描 20 条规则在本地实测
   p50≈288ms（见 `specs/004-scheduled-requests/quickstart.md` 的验证记录表），
   但容器限额下的表现尚未压测。


`Caddyfile` 是另一种入口方案（自动 HTTPS + WS 透传），适合公网部署：把
`FRONTEND_PORT` 关掉、由 Caddy 直接反代 `api:8000` 与 `frontend:80`。
默认部署走 frontend 内置 nginx，**不需要** Caddy。

## 日志与排查

生产（`APP_ENV=production`）输出结构化 JSON，每行一条：

```json
{"status":401,"duration_ms":11.8,"client_ip":"203.0.113.7","event":"request",
 "method":"GET","path":"/api/v1/agents","request_id":"a1b2c3d4e5f6a7b8",
 "level":"info","timestamp":"2026-11-14T10:54:22.177625Z"}
```

- **request_id**：每个请求唯一，同一请求内所有日志都带它。客户端可传
  `X-Request-ID` 复用（响应头也会回传），前端报错时带上即可精确定位。
- **脱敏**：`password` / `token` / `authorization` / `secret` / `api-key`
  等字段自动替换为 `***`（含嵌套结构）；query string 不落日志（WS 握手 token 在 query）。
- **降噪**：`/api/v1/health*` 与静态资源不写访问日志。
- uvicorn 自带 access log 已关闭（`--no-access-log`），避免与上面重复。

按请求聚合日志：

```bash
docker logs aw-api 2>&1 | grep '"request_id": "a1b2c3d4e5f6a7b8"'
```

## 数据备份

只用命名卷，目录在 `/var/lib/docker/volumes/aw_{pgdata,pwal,miniodata,redisdata}`。

```bash
# PostgreSQL 逻辑备份（推荐，可跨版本恢复）
docker exec aw-postgres pg_dump -U aw -Fc agent_workbench > backup-$(date +%F).dump
# 恢复
docker exec -i aw-postgres pg_restore -U aw -d agent_workbench --clean < backup-2026-11-14.dump
```

MinIO 附件建议额外同步 `miniodata` 卷或用 `mc mirror` 到外部存储。

### 时间点恢复（PITR，016 阶段补齐）

postgres 已开 WAL 归档（`archive_mode=on`，写入 `aw_pgwal` 卷的 `/wal-archive`），
配合 `deploy/backup/pg_basebackup.sh` 的基础备份可以恢复到任意时间点：

```bash
# 每天基础备份 + 每周恢复演练（脚本内含归档开关自检）
0 2 * * *   cd /opt/agent-workbench && BACKUP_DIR=/var/backups/aw deploy/backup/pg_basebackup.sh
0 4 * * 0   cd /opt/agent-workbench && BACKUP_DIR=/var/backups/aw deploy/backup/pitr_restore_drill.sh
```

演练脚本起一次性容器恢复并断言 alembic 版本与表数，**不碰生产库**。
完整说明（含按时间点恢复的手工步骤、`pgwal` 卷的删除风险）见
[`backup/README.md`](backup/README.md)。

> ⚠️ `docker compose down -v` 会连 `aw_pgwal` 归档一起删。清卷前先确认基础备份已落到卷外。

### 异常上报（Sentry，可选）

`SENTRY_DSN` 留空时应用完全不加载 SDK（零开销）。启用需要两步：
装 `.[sentry]` extra + 填 DSN。只填 DSN 没装依赖时应用照常启动，只留一条 warning。
见 `.env.example` 的「异常上报」段。

## 升级

```bash
git pull
docker compose -p aw -f deploy/docker-compose.yml build
docker compose -p aw -f deploy/docker-compose.yml up -d   # migrate 会先跑 alembic upgrade head
```

`migrate` 每次 `up` 都会执行，alembic 幂等，已是最新版本时为空操作。

### 构建期出网（Docker 内 DNS / 代理不通时）

症状：`build` 停在 `RUN uv sync --locked --no-dev --no-install-project`，报
`dns error: failed to lookup address information: Name does not resolve`，或换代理后变成
`tunnel error: unsuccessful`。

原因：构建容器（buildkit）的出网与 DNS 和宿主不是同一套。宿主机靠系统代理上网时，
容器里既没有该代理的配置，也未必能解析 PyPI 域名，于是 `uv` 下载 wheel 必然失败。
**逐层排查不要凭感觉**，先用一次性容器确认通道：

```bash
# 宿主代理端口设为 $PROXY（Docker Desktop 里用 host.docker.internal，不要用 127.0.0.1）
docker run --rm -e HTTPS_PROXY=$PROXY -e HTTP_PROXY=$PROXY python:3.12-slim python -c "
import urllib.request
for u in ['https://pypi.org/simple/', 'https://files.pythonhosted.org/']:
    try: print(u, urllib.request.urlopen(u, timeout=15).status)
    except Exception as e: print(u, 'FAIL', type(e).__name__, str(e)[:110])
"
```

- `pypi.org` 200、`files.pythonhosted.org` **404 也算通**（该域根路径本就 404，
  要看的是「有没有 HTTP 响应」）；
- 报 `Tunnel connection failed: 502` 说明代理把该域挡了，换代理节点，不是改 Dockerfile；
- 两个都通，再把代理交给构建：

```bash
docker compose -p aw -f deploy/docker-compose.yml build \
  --build-arg HTTP_PROXY=$PROXY --build-arg HTTPS_PROXY=$PROXY \
  --build-arg NO_PROXY=localhost,127.0.0.1,postgres,redis,minio,host.docker.internal \
  api worker beat migrate
SERVICES="api worker beat" SKIP_BUILD=1 bash deploy/deploy.sh   # 复用刚构建的镜像
```

两个坑：

1. `HTTP_PROXY` / `HTTPS_PROXY` 是**预定义 build args，参与缓存键**。一旦传入，
   `uv sync` 那层缓存必然失效、要重新下载全部依赖——首次构建因此会慢几分钟，属正常。
2. 代理程序（Clash 之类）默认可能只监听 `127.0.0.1`。容器经 `host.docker.internal`
   连过去时源 IP 不是回环地址，需要放开局域网访问（allow-lan）或确认监听在 `0.0.0.0`。

构建期代理只作用于 `RUN` 层，不会写进最终镜像的 `ENV`，运行时不受影响。

## 内置 Agent 模板（005）

`backend/scripts/seed_agent_templates.py` 幂等注入 4 个内置模板（通用问答 / 代码审查 /
数据摘要 / 资料检索）。换 LLM 端点后**重跑一次**，模板的模型会跟着 `LLM_MODEL` 更新
（已存在的同名模板被跳过，不会重复插入）：

```bash
cd backend
.venv/bin/python scripts/seed_agent_templates.py            # 实跑
.venv/bin/python scripts/seed_agent_templates.py --dry-run  # 只看会插入什么
```

为什么不写进迁移：迁移不导入应用代码（本仓库既有约定），且内置模板的模型必须跟随
`LLM_MODEL`——硬编码会把「换端点后所有内置模板都指向不存在的模型」固化进 schema。

## 验证脚本（verify/）

真实容器栈上的端到端验证，详见 `specs/001-core-workflow/quickstart.md`。

```bash
backend/.venv/bin/python deploy/verify/s1_s4_scenarios.py --base http://localhost:8080
backend/.venv/bin/python deploy/verify/s5_scenarios.py   --base http://localhost:8080 --invite <INVITE>
backend/.venv/bin/python deploy/verify/s6_agent_templates.py --base http://localhost:8080 --invite <INVITE>
NODE_PATH=$HOME/.workbuddy/binaries/node/workspace/node_modules \
  node deploy/verify/ui_s4_s5.cjs                        # 真实浏览器（拖拽 + 实时日志）
```

`mock-llm/` 是 S5 验证用的 OpenAI 兼容网关（关键词路由：`FAIL` / `补充信息` / 正常两轮 ReAct），
不参与生产镜像构建（后端镜像构建上下文是 `backend/`）。
