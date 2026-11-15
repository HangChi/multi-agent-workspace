# 部署（deploy/）

本目录提供「一条命令起全套」的 Docker Compose 编排，以及真实栈验证脚本。

## 服务清单

| 服务 | 容器名 | 端口 | 说明 |
|---|---|---|---|
| frontend | aw-frontend | `${FRONTEND_PORT:-8080}` | nginx 托管 SPA，反代 `/api` 与 `/ws` 到 api |
| api | aw-api | 仅容器内 8000 | FastAPI（uvicorn 2 worker），不暴露到宿主机 |
| worker | aw-worker | - | Celery Worker，执行 Run（`WORKER_CONCURRENCY`，默认 2） |
| beat | aw-beat | - | Celery Beat，周期任务调度 |
| migrate | aw-migrate | - | 一次性：`alembic upgrade head`，api/worker/beat 等它成功 |
| postgres | aw-postgres | `127.0.0.1:5432` | PostgreSQL 16，仅本机可连 |
| redis | aw-redis | `127.0.0.1:6379` | 队列 / 事件 Pub-Sub / 结果后端 |
| minio | aw-minio | `127.0.0.1:9000` / `9001` | S3 兼容对象存储（附件与制品） |
| llm-gateway | aw-llm-gateway | 仅容器内 4000 | **仅验证用** Mock LLM，`--profile mock` 才启动 |

## 启动

```bash
cp deploy/.env.example deploy/.env     # 按需修改密钥（生产必改）
docker compose -p aw -f deploy/docker-compose.yml up -d --build
```

> 项目名固定为 `aw`（容器名 aw-*）。不加 `-p aw` 时 compose 会按目录名推导项目名，
> 与已有容器冲突（`container name already in use`）。

本地离线验证（无真实 LLM 密钥时）：

```bash
docker compose -p aw -f deploy/docker-compose.yml --profile mock up -d --build
# 并把 .env 的 LLM_BASE_URL 指向 http://llm-gateway:4000
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

003 的可观测性 API 提供管理员诊断用的计数器和近期告警；当前实现是进程内有界计数，不是持久化或跨 worker 聚合的生产指标。上线前应通过目标部署的 metrics/log collector 验证告警阈值与聚合行为。Fixture 并发测试不代表真实 Worker/API 栈验收，也未测量核心 API P95。

当前 003 release gate 状态和已验证结果见 [`specs/003-mcp-tool-market/release-status.md`](../specs/003-mcp-tool-market/release-status.md)。截至该报告日期，真实部署并发/P95 smoke、disposable DB migration downgrade/upgrade 尚未完成；Feature 002 native Linux/full-stack acceptance 仍是独立前置条件，不能由 003 测试替代。

## HTTPS 入口（可选）

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

只用命名卷，目录在 `/var/lib/docker/volumes/aw_{pgdata,miniodata,redisdata}`。

```bash
# PostgreSQL 逻辑备份（推荐，可跨版本恢复）
docker exec aw-postgres pg_dump -U aw -Fc agent_workbench > backup-$(date +%F).dump
# 恢复
docker exec -i aw-postgres pg_restore -U aw -d agent_workbench --clean < backup-2026-11-14.dump
```

MinIO 附件建议额外同步 `miniodata` 卷或用 `mc mirror` 到外部存储。

## 升级

```bash
git pull
docker compose -p aw -f deploy/docker-compose.yml build
docker compose -p aw -f deploy/docker-compose.yml up -d   # migrate 会先跑 alembic upgrade head
```

`migrate` 每次 `up` 都会执行，alembic 幂等，已是最新版本时为空操作。

## 验证脚本（verify/）

真实容器栈上的端到端验证，详见 `specs/001-core-workflow/quickstart.md`。

```bash
backend/.venv/bin/python deploy/verify/s1_s4_scenarios.py --base http://localhost:8080
backend/.venv/bin/python deploy/verify/s5_scenarios.py   --base http://localhost:8080
NODE_PATH=$HOME/.workbuddy/binaries/node/workspace/node_modules \
  node deploy/verify/ui_s4_s5.cjs                        # 真实浏览器（拖拽 + 实时日志）
```

`mock-llm/` 是 S5 验证用的 OpenAI 兼容网关（关键词路由：`FAIL` / `补充信息` / 正常两轮 ReAct），
不参与生产镜像构建（后端镜像构建上下文是 `backend/`）。
