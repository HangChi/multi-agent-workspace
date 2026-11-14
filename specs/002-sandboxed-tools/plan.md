# Implementation Plan: 沙箱代码执行与工具授权（Sandboxed Tools）

**Branch**: `feat/sandboxed-tools` | **Date**: 2026-11-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-sandboxed-tools/spec.md`

**Note**: 本 plan 由 `/speckit-plan` 契约生成；Phase 0 产出 `research.md`（R-01~R-10），Phase 1 产出 `data-model.md` / `contracts/api.md` / `quickstart.md`。

## Summary

补齐 Phase 2 最大的能力空洞，并把 001 遗留的「工具白名单形同虚设」一并收口。两件事：

1. **隔离执行**：Worker 通过 Docker SDK 起一次性容器执行 Python/shell 脚本，资源限额（CPU/内存/磁盘/进程数）+ 时长上限 + 取消即杀；输出截断回传，`output/` 目录回收为 MinIO 制品。
2. **工具授权生效**：把三份互相矛盾的清单收敛为 `app/executors/catalog.py` 单一事实源，运行时按 `agents.tools` 注入执行器，越权调用被拒并留痕。

出站策略按 spec 修订版落地：**默认放行 + 全量记录**（DNS 代理记域名、conntrack 记真实目标 IP:port）、**全局黑名单用 iptables 强制**（内网 / `169.254.0.0/16` / 内部服务）、**Agent 级限制模式**由成员自助收紧。

## Technical Context

**Language/Version**: Python 3.12+（后端）；TypeScript 5.x（前端）

**Primary Dependencies**: 后端新增 `docker>=7.1`（Docker SDK）；复用 FastAPI / SQLAlchemy 2.0 async / Alembic / Celery 5 / Redis / MinIO SDK / structlog；沙箱侧 `aw-sandbox-python:3.12` 镜像（内置 numpy/pandas/matplotlib/requests/beautifulsoup4/openpyxl）；基础设施新增 `dns-proxy`（受控 DNS，记录查询）+ `sbx-net` init 容器镜像（`--net=host --cap-add=NET_ADMIN`，只跑 iptables/conntrack 命令）

**Storage**: PostgreSQL 16（新增 4 张表 + 2 处列变更）；MinIO（沙箱产物，键 `artifacts/{run_id}/{execution_id}/{filename}`）；Redis（并发信号量 `sbx:sem:*`、既有 Pub/Sub）

**Testing**: pytest（单元：catalog/授权注入/出站求值；集成：执行器生命周期、越权矩阵、黑名单求值）；vitest（工具目录渲染、出网区块、执行卡片）；真实栈脚本 `deploy/verify/*.py`

**Target Platform**: Linux 服务器（Docker Compose）。**宿主必须是 Linux**：iptables/conntrack 方案在 macOS/Windows Docker Desktop 上语义不同，本地开发需在 Linux 或云主机验证

**Project Type**: Web application（在既有 monorepo 内扩展，不新增项目）

**Performance Goals**: 简单脚本调用→结果 ≤5s（镜像已预热）；20 并发脚本执行下其他接口 P95 劣化 ≤20%；单次执行容器创建+销毁开销 ≤800ms

**Constraints**: 单机沙箱并发默认 4（可配）；单次执行默认 120s / 1 CPU / 512MB / 256MB 磁盘 / 128 进程；stdout 保留尾部 1MB；制品单文件 20MB、总量 50MB、50 个

**Scale/Scope**: 团队规模（<100 用户），上百并发任务，单机单执行节点

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | 宪法条款 | 状态 | 说明 |
|---|---------|------|------|
| I | SDD+TDD 双驱动（NON-NEGOTIABLE） | ✅ 通过 | 本 plan 为 SDD 产物；tasks 阶段强制测试先红后绿，越权矩阵与黑名单求值必须先写失败用例 |
| II | 规格先行，实现后置 | ✅ 通过 | spec 已评审（5 US / 28 FR / 10 SC），含显式「不含」7 项 |
| III | 完整系统，非 MVP | ✅ 通过 | 本特性完成后 P2「跑脚本」验收口径可达成，P2 可独立上线 |
| IV | 质量门禁 | ✅ 通过 | ruff + mypy + pytest + eslint + tsc + vitest + CI 全链路；关键路径（沙箱生命周期、越权拒绝、黑名单求值）必须有集成测试 |
| 技术栈 | FastAPI + SQLAlchemy async + Alembic + Celery + Redis | ✅ 通过 | 沿用，新增 `docker` SDK 属实现依赖，不改变栈 |
| 技术栈 | React18 + TS + Vite + Tailwind + dnd-kit + TanStack Query + Zustand | ✅ 通过 | 前端只改既有页面，不新增路由 |
| 技术栈 | PostgreSQL16 / Redis / MinIO | ✅ 通过 | 新表落 PG，产物落 MinIO |
| 技术栈 | LiteLLM 网关统一接入 | ✅ 通过 | 沙箱工具不直连模型；router 策略不受影响 |
| 技术栈 | 自研编排器，禁 LangGraph | ✅ 通过 | 沙箱作为 ToolExecutor 接入既有 ReAct 循环，不触碰编排模型 |
| 技术栈 | **代码沙箱 Docker 一次性容器** | ✅ 通过 | R-01：SDK 创建独立网络 + 一次性容器，执行后销毁 |
| 技术栈 | API 层只入队，RT<100ms | ✅ 通过 | 沙箱只在 Worker 内执行；`GET /tools`、`egress-rules` 为轻查询 |
| 技术栈 | WS 实时推送 | ✅ 通过 | 新增 5 类事件走既有 Redis Pub/Sub → WS 通道 |
| 技术栈 | Docker Compose + Caddy | ✅ 通过 | 新增 `dns-proxy` 服务与 worker 的 socket 挂载，仍单机 Compose |
| 安全 | JWT + RBAC 三角色 | ✅ 通过 | 黑名单维护限 admin；Agent 级规则限 owner/admin |
| 安全 | 凭证 Fernet 加密 | ⚪ N/A | 本特性不新增任何外部凭证；沙箱**不注入**任何应用密钥（只注入白名单环境变量） |
| 安全 | **用户脚本沙箱隔离** | ✅ 通过 | FR-006~014：独立网络/文件系统/进程空间 + 资源限额 + 时长上限 |
| 安全 | 成本上限+月配额+熔断 | ✅ 通过 | 沙箱不消耗 token；新增执行配额（并发/时长）作为资源维度的熔断 |
| 安全 | 审计日志 | ✅ 通过 | 新增 `sandbox_exec` / `tool_denied` / `egress_denied` 三类动作 |
| 安全 | 日志脱敏 | ✅ 通过 | 脚本内容与 stdout 不进结构化日志（只进 DB 与审计摘要）；沿用既有脱敏 |
| 安全 | 资源归属校验 | ✅ 通过 | 执行详情/制品/出站记录均校验 Run.owner_id |
| 安全 | 内核级隔离加固（seccomp/gVisor） | ⚪ N/A | 宪法 Follow-up TODO 明确归 P3；本特性只做资源与网络隔离，已在 spec/checklist 记录该偏差 |
| 工作流 | 每特性一分支 | ✅ 通过 | `feat/sandboxed-tools` |
| 工作流 | 提交规范 | ✅ 通过 | spec 已提交 `docs(spec)`；实现每 Phase 一次 `feat()` 提交 |
| 工作流 | 偏离时改上游文档 | ✅ 通过 | D-1~D-6 偏差已写入 data-model.md；实现期若再偏离，回头改 spec/plan |
| 工作流 | tasks.md 如实更新 | ✅ 通过 | implement 阶段按 Phase 勾选，未跑过保持未勾 |

**Gate 评估**：无未决 violation。两项 ⚪ N/A 均有宪法明文依据（内核加固归 P3；本特性不引入新凭证）。**Constitution Check 通过。**

**Phase 1 后复查**：设计产出后重跑本表，结论不变；新增确认一项——R-01 的 socket 挂载带来「Worker 等价宿主 root」风险，属宪法「安全加固复核（P3）」范围，已在该行与 research R-01 双向记录。

## Project Structure

### Documentation (this feature)

```text
specs/002-sandboxed-tools/
├── plan.md              # This file
├── spec.md              # 已完成（28 FR / 10 SC）
├── research.md          # Phase 0：R-01~R-10
├── data-model.md        # Phase 1：4 新表 + 2 变更 + D-1~D-6
├── contracts/api.md     # Phase 1：REST/WS/内部工具契约
├── quickstart.md        # Phase 1：S1~S6 验证场景 + 越权矩阵
├── checklists/requirements.md
└── tasks.md             # Phase 2 产出（/speckit-tasks，尚未生成）
```

### Source Code (repository root)

只列新增/变更文件（`+` 新增，`~` 变更）：

```text
backend/
├── app/
│   ├── executors/
│   │   ├── catalog.py               + 工具目录单一事实源（5 工具声明）
│   │   ├── base.py                  ~ 增加 from_names() 与 denied 结果类型
│   │   ├── sandbox.py               + run_python / run_shell 执行器（SDK 调用）
│   │   ├── sandbox_client.py        + 容器/网络生命周期封装（create/wait/kill/archive）
│   │   ├── egress.py                + 出站策略求值与 iptables/conntrack 编排
│   │   └── http.py                  ~ default_executors() 改为读 catalog
│   ├── services/
│   │   ├── sandbox_service.py       + 执行记录落库、配额信号量、制品回收
│   │   ├── egress_service.py        + 规则 CRUD、求值、访问记录批量写入
│   │   └── artifact_service.py      ~ 新增 put_bytes()（服务端直传 + sha256）
│   ├── api/v1/
│   │   ├── tools.py                 + GET /tools
│   │   ├── agents.py                ~ tools 校验读目录 + egress_mode 字段
│   │   ├── agent_egress.py          + Agent 级出站规则 CRUD
│   │   ├── runs.py                  ~ 执行详情/出站记录端点
│   │   ├── admin.py                 ~ 黑名单 CRUD + 访问记录检索
│   │   └── ws.py                    ~ 新增 5 类事件透传
│   ├── models/
│   │   ├── tool.py                  + tools
│   │   ├── sandbox_execution.py     + sandbox_executions
│   │   ├── egress.py                + egress_rules / egress_accesses
│   │   ├── agent.py                 ~ egress_mode
│   │   └── artifact.py              ~ sha256
│   ├── schemas/
│   │   ├── tool.py                  + 目录与执行详情 DTO
│   │   ├── sandbox.py               + 执行摘要/详情/出站记录 DTO
│   │   ├── egress.py                + 规则 DTO
│   │   └── agent.py                 ~ tools 校验改目录、egress_mode
│   ├── core/config.py               ~ SBX_* / EGRESS_* / TOOL_DENY_THRESHOLD
│   ├── orchestrator/runner.py       ~ 按白名单注入 + denied 结果 + should_abort 透传
│   └── tasks/execute_run.py         ~ build_executors(agent.tools)、沙箱取消回调接线
├── alembic/versions/
│   └── 0003_sandbox_tools.py        + 建 4 表 + 加 2 列 + tools seed + 工具名迁移
└── tests/
    ├── unit/                        + test_catalog / test_egress_eval / test_sandbox_limits
    └── integration/                 + test_sandbox_flow / test_tool_denied / test_egress_record

deploy/
├── docker-compose.yml               ~ worker 挂 socket + dns 指向 + dns-proxy 服务
├── sandbox/Dockerfile               + aw-sandbox-python:3.12
├── sbx-net/Dockerfile               + init 容器（iptables + conntrack）
├── dns-proxy/                       + 受控 DNS（查询记录 + 黑名单 NXDOMAIN）
└── verify/
    ├── s3_sandbox_scenarios.py      + S1/S2/S3/S4 真实栈断言
    └── s5_egress_scenarios.py       + S5 出站断言

frontend/src/
├── api/endpoints.ts                 ~ tools/executions/egress 客户端
├── components/agent/
│   ├── constants.ts                 ~ 删除硬编码工具清单（改从 /tools 拉取）
│   ├── AgentForm.tsx                ~ 工具来源改为接口 + 出网区块
│   └── EgressRules.tsx              + 域名清单编辑
├── components/run/
│   ├── ExecutionCard.tsx            + 代码/输出/资源/失败分类
│   └── EgressList.tsx               + 本次出站访问
├── pages/RunDetailPage.tsx          ~ 接入 ExecutionCard
├── pages/AdminPage.tsx              ~ 黑名单维护 + 访问记录检索
└── hooks/useRunSocket.ts            ~ 5 类新事件的 reducer
```

**Structure Decision**: 沿用 001 的 monorepo 结构，全部在既有目录内扩展，不新增顶层目录。沙箱能力集中三处：`executors/`（执行）、`services/sandbox_service.py`（编排与落库）、`deploy/`（镜像与网络设施）。把「容器生命周期」与「策略求值」分开（`sandbox_client.py` / `egress.py`），是为了让越权矩阵能在不启容器的情况下做单元测试——这是本特性 TDD 能否跑得动的关键。

## Complexity Tracking

> Constitution Check 无 violation，以下为关键技术复杂度的论证。

| 决策 | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 通过宿主 Docker socket 控制沙箱 | 宪法要求一次性容器；socket 方案无需 privileged、无需额外守护 | DinD 需 `--privileged` 且镜像层重复拉取，与「资源上限」冲突；API 层直接起容器会让重活离开 Worker，违反「API 只入队」 |
| 独立 `dns-proxy` 服务 + 每执行独立 bridge 网络 | SC-009/SC-010 要求覆盖直连 IP、非常规端口并 100% 拦截黑名单 | 仅 DNS 记录挡不住直连 IP；透明代理需注入 CA 且可被忽略 `HTTP_PROXY` 绕过 |
| 用 init 容器执行 iptables/conntrack | Worker 自身不拿 `NET_ADMIN`/host netns，权限面更小 | 给 Worker 加 `cap_add: NET_ADMIN` + `network_mode: host` 会同时打断它连 PG/Redis 的 compose 网络 |
| 新建 `sandbox_executions` 表 | stdout 可达 1MB，塞 `steps.output` 会污染详情接口、WS 推送与统计索引 | 复用 `steps` 加 8 个专用列会污染所有工具共用的表；仅靠 jsonb 无法做配额聚合 |
| 工具目录落库（`tools` 表）+ 代码常量双写 | 需要「管理员临时下线工具」的运营能力，且前端要单一来源 | 只放代码：无法运行时下线；只放 DB：启动顺序耦合、迁移期易空目录 |
| 出站记录批量落库（执行结束时一次写入） | 单次执行通常几十条连接，逐条实时写会放大写入 | 实时逐条写能在运行中看进度，但代价是每执行几十次写；v1 以「执行结束可见」为可接受粒度 |
