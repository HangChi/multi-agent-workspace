# Implementation Plan: 核心工作流（Core Workflow）

**Branch**: `feat/core-workflow` | **Date**: 2026-11-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-core-workflow/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

Phase 1 核心闭环：为「拖拽式任务分发 + 多角色 Agent 执行」系统搭建可上线的基础骨架——用户认证与三角色 RBAC、Agent 与 Agent 组管理、需求创建与看板、拖拽分配、单 Agent LLM 执行（ReAct 循环）、WebSocket 实时运行反馈。技术路线：FastAPI（async）+ PostgreSQL + Celery/Redis 队列 + LiteLLM 网关 + 自研轻量编排器 + React 前端。

## Technical Context

**Language/Version**: Python 3.12+（后端）；TypeScript 5.x（前端）

**Primary Dependencies**: 后端 FastAPI + SQLAlchemy 2.0 (async) + Alembic + Celery 5 + Redis + LiteLLM + Pydantic v2 + structlog；前端 React 18 + Vite + TailwindCSS + dnd-kit + TanStack Query + Zustand

**Storage**: PostgreSQL 16（结构化核心数据）；Redis 7（队列/缓存/分布式锁/Pub-Sub）；MinIO（S3 兼容，附件与制品存储）

**Testing**: pytest（后端，含 async 支持 + pytest-cov）；vitest + React Testing Library（前端）

**Target Platform**: Linux 服务器（Docker Compose 部署，Caddy HTTPS）

**Project Type**: Web application（前后端分离的 SPA + REST API + WebSocket + 异步任务队列）

**Performance Goals**: 拖拽分配接口 RT < 100ms；运行日志推送延迟 < 2s；同时支撑 20+ 并发执行

**Constraints**: 单机 Docker Compose 起步可支撑数百并发；密钥不落镜像不入库；凭证加密存储；日志脱敏

**Scale/Scope**: 团队规模（<100 用户），上百并发任务，单机多容器部署

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | 宪法条款 | 状态 | 说明 |
|---|---------|------|------|
| I | SDD+TDD 双驱动（NON-NEGOTIABLE） | ✅ 通过 | 本 plan 即 SDD 的 plan 产物；TDD 在 tasks/implement 阶段强制：测试先红后绿 |
| II | 规格先行，实现后置 | ✅ 通过 | spec.md 已评审通过（5 US/21 FR/8 SC），本 plan 基于 spec 展开 |
| III | 完整系统，非 MVP | ✅ 通过 | 本特性为 Phase 1 核心闭环，交付即「可独立上线的完整切片」 |
| IV | 质量门禁（lint+类型检查+测试） | ✅ 通过 | plan 中锁定 pytest/vitest；关键路径（分配/状态机/成本熔断）有集成测试要求 |
| 技术栈 | FastAPI+SQLAlchemy async+Alembic+Celery+Redis | ✅ 通过 | 见 Technical Context，与宪法一致 |
| 技术栈 | React18+TS+Vite+Tailwind+dnd-kit+TanStack Query+Zustand | ✅ 通过 | 见 Technical Context |
| 技术栈 | PostgreSQL16 / Redis / MinIO | ✅ 通过 | 见 Storage |
| 技术栈 | LiteLLM 网关统一接入 | ✅ 通过 | LLM 执行器经 LiteLLM，多模型可切换 |
| 技术栈 | 自研编排器，禁 LangGraph | ✅ 通过 | 编排器为自研状态机 + ReAct 循环 |
| 技术栈 | 代码沙箱 Docker 一次性容器 | ⚪ N/A | 本特性（Phase 1）不含代码沙箱执行器，属 Phase 2，无适用面 |
| 技术栈 | API 层只入队，RT<100ms | ✅ 通过 | 分配接口仅做校验+入队，重活交 Worker |
| 技术栈 | WS 实时推送 | ✅ 通过 | 运行详情通过 WebSocket 推送状态与日志 |
| 技术栈 | Docker Compose + Caddy | ✅ 通过 | 部署层见 deploy/ |
| 安全 | JWT + RBAC 三角色 | ✅ 通过 | 认证模块实现 access+refresh JWT；admin/member/viewer |
| 安全 | 凭证 Fernet 加密 | ⚪ N/A | 工具凭证管理属 Phase 2 工具系统，本特性无外部凭证 |
| 安全 | 用户脚本沙箱隔离 | ⚪ N/A | 代码沙箱属 Phase 2 |
| 安全 | 成本上限+月配额+熔断 | ✅ 通过 | FR-019：Agent 成本上限实时累计并熔断 |
| 安全 | 审计日志 | ✅ 通过 | FR-020：敏感操作全量审计 |
| 安全 | 日志脱敏 | ✅ 通过 | structlog 配置敏感字段脱敏 |
| 安全 | 资源归属校验 | ✅ 通过 | 所有资源操作校验 owner_id |
| 工作流 | 每特性一分支 | ✅ 通过 | 当前分支 feat/core-workflow |
| 工作流 | 提交规范 | ✅ 通过 | spec 提交 docs(spec)；实现每 Phase 一次 feat() 提交 |
| 工作流 | 偏离时改上游文档 | ✅ 通过 | 实现偏离 spec/plan 时回头改并记录 |
| 工作流 | tasks.md 如实更新 | ✅ 通过 | implement 阶段按 Phase 更新 |

**Gate 评估**：无未决 violation，全部通过或无适用面（⚪ 已说明原因）。Constitution Check 通过。

## Project Structure

### Documentation (this feature)

```text
specs/001-core-workflow/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── main.py              # FastAPI 入口（含 WS 路由）
│   ├── api/                 # 路由层（auth/agents/groups/requests/runs/ws/audit/stats）
│   │   ├── v1/
│   │   │   ├── auth.py
│   │   │   ├── agents.py
│   │   │   ├── groups.py
│   │   │   ├── requests.py
│   │   │   ├── runs.py
│   │   │   ├── ws.py
│   │   │   └── stats.py
│   │   └── deps.py          # 依赖注入（当前用户/权限/资源归属）
│   ├── core/
│   │   ├── config.py        # pydantic-settings
│   │   ├── security.py      # JWT 签发/校验、密码哈希
│   │   ├── db.py            # async engine/session
│   │   ├── audit.py         # 审计日志写入
│   │   └── events.py        # Redis Pub/Sub 事件发布
│   ├── models/              # SQLAlchemy 模型
│   │   ├── user.py
│   │   ├── agent.py
│   │   ├── group.py
│   │   ├── request.py
│   │   ├── run.py
│   │   ├── step.py
│   │   └── artifact.py
│   ├── schemas/             # Pydantic 模型
│   │   ├── auth.py
│   │   ├── agent.py
│   │   ├── group.py
│   │   ├── request.py
│   │   ├── run.py
│   │   └── common.py
│   ├── services/            # 业务逻辑
│   │   ├── auth_service.py
│   │   ├── agent_service.py
│   │   ├── group_service.py
│   │   ├── request_service.py
│   │   ├── assignment_service.py   # 分配/入队
│   │   ├── quota_service.py        # 配额/成本熔断
│   │   └── artifact_service.py     # MinIO 上传/下载
│   ├── orchestrator/        # 编排核心
│   │   ├── state_machine.py # Run 状态机
│   │   ├── runner.py        # 单 Agent ReAct 执行循环
│   │   └── strategies.py    # 组路由策略（direct/fan_out）
│   ├── executors/
│   │   ├── base.py          # ToolExecutor 协议
│   │   ├── llm.py           # LiteLLM 封装
│   │   └── http.py          # HTTP 工具
│   ├── tasks/               # Celery 任务
│   │   ├── celery_app.py
│   │   ├── execute_run.py
│   │   └── events.py        # Worker→API 状态回调
│   └── worker.py            # Celery Worker 入口
├── alembic/                 # 数据库迁移
│   ├── env.py
│   └── versions/
├── tests/
│   ├── conftest.py
│   ├── unit/                # 单元测试（状态机/安全/服务）
│   └── integration/         # 集成测试（API/分配/执行）
├── pyproject.toml
├── pytest.ini
└── Dockerfile

frontend/
├── src/
│   ├── api/                 # API 客户端（axios + TanStack Query）
│   │   ├── client.ts
│   │   └── endpoints.ts
│   ├── components/
│   │   ├── board/           # 看板 + 拖拽
│   │   │   ├── Board.tsx
│   │   │   ├── RequestCard.tsx
│   │   │   ├── AgentCard.tsx
│   │   │   ├── GroupCard.tsx
│   │   │   └── AssignModal.tsx
│   │   ├── agent/           # Agent 管理
│   │   ├── group/           # 组管理
│   │   ├── run/             # 运行详情（WS 日志流）
│   │   │   ├── RunDetail.tsx
│   │   │   └── StepList.tsx
│   │   └── ui/              # 通用组件
│   ├── hooks/
│   │   ├── useAuth.ts
│   │   ├── useRequests.ts
│   │   └── useRunSocket.ts  # WebSocket 订阅
│   ├── stores/              # Zustand
│   │   ├── authStore.ts
│   │   └── boardStore.ts
│   ├── pages/
│   │   ├── LoginPage.tsx
│   │   ├── RegisterPage.tsx
│   │   ├── BoardPage.tsx    # 核心工作台
│   │   ├── AgentPage.tsx
│   │   ├── GroupPage.tsx
│   │   ├── RunDetailPage.tsx
│   │   ├── DashboardPage.tsx
│   │   └── AdminPage.tsx
│   ├── App.tsx
│   ├── router.tsx
│   └── main.tsx
├── index.html
├── package.json
├── vite.config.ts
├── tsconfig.json
├── tailwind.config.js
└── Dockerfile

deploy/
├── docker-compose.yml       # postgres/redis/minio/api/worker/beat/frontend
├── Caddyfile                # HTTPS 反向代理
├── .env.example             # 环境变量模板（不含真实密钥）
└── README.md

docs/
└── architecture.md          # 已存在，系统架构总览
```

**Structure Decision**: 采用前后端分离 + 独立 worker 的 monorepo 结构。后端 `backend/` 内含 FastAPI 应用（api/core/models/schemas/services）、编排核心（orchestrator）、执行器（executors）与 Celery 任务（tasks），职责清晰互不耦合；前端 `frontend/` 为 React SPA；`deploy/` 承载部署编排。此结构对齐宪法「API 层只入队、重活交 Worker」与「可横向扩展」要求——worker 作为独立容器可单独扩容。docs/ 与 specs/ 为文档目录，不入代码结构。

## Complexity Tracking

> Constitution Check 无 violations，以下为关键技术复杂度的论证（非 violation，属设计论证）。

| 决策 | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 自研编排器（状态机+ReAct） | 任务形态多样（非纯对话），需在 Run 粒度控制状态流转、步骤记录、成本熔断 | 直接调 LLM 无状态循环无法满足 FR-014~019（实时步骤、成本累计、可取消、等待补充）；LangGraph 等框架锁死编排模型且偏对话流 |
| Celery + Redis 队列 | 上百并发横向扩展、优先级队列、重试/幂等、Beat 定时（后续 Phase）生态成熟 | Arq/Dramatiq 生态弱；直接 asyncio 并发不满足「API 只入队、Worker 独立扩容」的宪法约束 |
| WebSocket + Redis Pub/Sub 事件桥 | API 多进程 + Worker 独立进程，状态变更需实时广播到前端 | 前端轮询延迟高（>2s 违反 SC-003）；单进程内存事件队列无法跨 Worker 通知 API |
| SQLAlchemy async + Alembic | 异步 API 栈全链路 async，迁移版本化可上线 | 同步 ORM 会阻塞事件循环；无迁移工具则上线无法演进 schema |
