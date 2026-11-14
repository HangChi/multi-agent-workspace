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

## HTTPS 入口（可选）

`Caddyfile` 是另一种入口方案（自动 HTTPS + WS 透传），适合公网部署：把
`FRONTEND_PORT` 关掉、由 Caddy 直接反代 `api:8000` 与 `frontend:80`。
默认部署走 frontend 内置 nginx，**不需要** Caddy。

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
