# Agent 工作台系统

「拖拽式任务分发 + 多角色 Agent 执行」的工作台系统：用户创建带角色/提示词/工具集的 Agent，自由组合成 Agent 组，把需求拖到 Agent/组上即触发编排执行，全过程实时可见。

## 技术栈

- **后端**：FastAPI + SQLAlchemy 2.0 (async) + Alembic + Celery 5 + Redis + LiteLLM
- **前端**：React 18 + TypeScript + Vite + TailwindCSS + dnd-kit + TanStack Query + Zustand
- **存储**：PostgreSQL 16 + Redis 7 + MinIO (S3)
- **部署**：Docker Compose + Caddy (HTTPS)

## 目录结构

```text
backend/    # FastAPI 后端（api/core/models/schemas/services/orchestrator/executors/tasks）
frontend/   # React SPA
deploy/     # docker-compose / Caddyfile / .env.example
docs/       # 架构文档
specs/      # Spec Kit 规格（SDD+TDD 工作流）
```

## 本地启动

1. 复制环境变量：`cp deploy/.env.example .env` 并修改密钥
2. 启动基础设施：`docker compose -f deploy/docker-compose.yml up -d`（postgres/redis/minio）
3. 后端：`cd backend && pip install -e ".[dev]" && alembic upgrade head && uvicorn app.main:app --reload`
4. 前端：`cd frontend && npm install && npm run dev`

详见 `specs/001-core-workflow/quickstart.md` 验证场景。
