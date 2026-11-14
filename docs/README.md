# Agent 工作台系统

[![CI](https://github.com/HangChi/multi-agent-workspace/actions/workflows/ci.yml/badge.svg)](https://github.com/HangChi/multi-agent-workspace/actions/workflows/ci.yml)

「拖拽式任务分发 + 多角色 Agent 执行」的工作台系统：用户创建带角色/提示词/工具集的 Agent，
自由组合成 Agent 组，把需求拖到 Agent/组上即触发编排执行，全过程实时可见。

## 技术栈

- **后端**：FastAPI + SQLAlchemy 2.0 (async) + Alembic + Celery 5 + Redis + LiteLLM
- **前端**：React 18 + TypeScript + Vite + TailwindCSS + dnd-kit + TanStack Query + Zustand
- **存储**：PostgreSQL 16 + Redis 7 + MinIO (S3)
- **部署**：Docker Compose + nginx（frontend 容器内置；`deploy/Caddyfile` 是可选 HTTPS 入口）

## 目录结构

```text
backend/    # FastAPI 后端（api/core/models/schemas/services/orchestrator/executors/tasks）
frontend/   # React SPA（nginx 托管 + 反代 /api、/ws）
deploy/     # docker-compose / .env.example / mock-llm / verify（真实栈验证脚本）
docs/       # 架构文档
specs/      # Spec Kit 规格（SDD+TDD 工作流）
```

## 启动方式

### A. 全栈 Docker（推荐，一条命令）

```bash
cp deploy/.env.example deploy/.env                 # 改密钥；生产务必替换 JWT_SECRET/INVITE_CODE
docker compose -p aw -f deploy/docker-compose.yml up -d --build
# 访问 http://localhost:8080
```

无真实 LLM 密钥时可加 `--profile mock` 启动内置 Mock 网关，并把
`deploy/.env` 的 `LLM_BASE_URL` 设为 `http://llm-gateway:4000`。

> 项目名必须是 `aw`（否则容器名冲突）；`.env` 要放在 `deploy/` 下。

### B. 本地开发（前后端热更新）

```bash
# 1. 基础设施
docker compose -p aw -f deploy/docker-compose.yml up -d postgres redis minio
# 2. 后端
cd backend && pip install -e ".[dev]" && alembic upgrade head
uvicorn app.main:app --reload --port 8000
# 3. 前端（Vite dev server 代理 /api 到 127.0.0.1:8000）
cd frontend && npm install && npm run dev
```

首个 admin：`cd backend && python scripts/bootstrap.py`（生成管理员邀请码）。

## 测试与质量门禁

CI（`.github/workflows/ci.yml`）在每次 push/PR 跑：后端 ruff + mypy + pytest，
前端 eslint + tsc + vitest + build（并断言 CSS 产物 >5KB，防 Tailwind 配置丢失）。
本地等价命令：

```bash
# 后端（测试库需先建：createdb agent_workbench_test）
cd backend
DATABASE_URL="postgresql+asyncpg://aw:<密码>@127.0.0.1:5432/agent_workbench_test" python -m pytest -q
python -m ruff check app tests && python -m mypy app

# 前端
cd frontend && npm run lint && npm run typecheck && npm test
```

> pytest 必须显式传 `DATABASE_URL` 指向 `_test` 库，否则 conftest 会回落到代码内默认口令。

端到端验证场景见 `specs/001-core-workflow/quickstart.md`；部署细节见 `deploy/README.md`。
