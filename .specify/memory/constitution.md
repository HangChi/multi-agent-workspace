<!--
Sync Impact Report
- Version change: 0.0.0 (template scaffold) → 1.0.0
- Modified principles: 完全重写模板占位符，按本项目定位落地
- Added sections: 核心原则、技术栈与架构约束、合规/安全边界、开发工作流与质量门禁
- Follow-up TODOs:
  - 组编排策略的「router(智能路由)」实现细节（LLM 选路 vs 规则选路）待 Phase 2 细化
  - 代码沙箱的 Docker 隔离基线（seccomp/gVisor 路径）待 Phase 3 加固时定
  - 是否需要引入图执行引擎支撑复杂多 Agent 协作，Phase 2 结束后评审
- Version change: 1.0.0 → 1.1.0（2026-11-18，007-request-dispatch 开工时经用户确认）
- Modified sections: 开头定位段（系统定位纳入「需求分派给员工」闭环）
- Rationale: 001–006 落地后审计发现系统实际只有「谁建需求谁拖给 Agent」，
  与设计动机「公司内部系统：员工接收需求后再拖给 Agent」存在结构性偏差。
  007 补齐该闭环，宪法定位随之更新；不更新则后续每份 spec 的 Constitution Check
  都带着旧定位核对。
- Impact assessment: 仅定位描述变更，无原则条款增删；既有 I–VIII 原则与全部
  MUST 条款不受影响，无需回溯修订 001–006 的 spec/plan。
-->

# Agent 工作台系统 Constitution

「拖拽式任务分发 + 多角色 Agent 执行」的工作台系统：用户创建带角色/提示词/工具集的 Agent，自由组合成 Agent 组；需求可分派给具体员工，由员工接收后拖到 Agent/组上触发编排执行（员工也可直接处理自己创建的需求），全过程实时可见。**本宪法是全仓库最高约束**，所有 spec、plan、实现都必须遵守，不得以任何理由绕过。

## Core Principles

### I. SDD + TDD 双驱动（NON-NEGOTIABLE）
- 每个特性 MUST 先写功能规格（spec），经评审确认后才进入实现，禁止「边写边想」。
- 每个特性 MUST 遵循 Spec Kit 四步：specify → plan → tasks → implement。
- 所有业务逻辑 MUST 先写测试（红）再实现（绿），Red-Green-Refactor 循环严格强制。
- 测试未通过前，实现代码 MUST NOT 被标记为完成。
- 每次提交 MUST 通过当阶段质量门禁（见 IV）。

### II. 规格先行，实现后置
- 需求 MUST 先以用户故事（User Story）+ 功能需求（FR）+ 验收标准（SC）形式写入 `specs/NNN-<name>/spec.md`。
- spec 只描述 WHAT 与 WHY，禁止泄漏 HOW（不得出现技术栈/框架/API 细节）。
- 每条 FR MUST 有可判定的验收锚点（Given/When/Then 场景），禁止「需求在 Edge Cases 提到就算有验收」。
- 范围边界 MUST 显式写「不含」，防止范围在 plan/tasks 阶段悄悄膨胀。
- 未决事项最多 3 个且 MUST 标注 `[NEEDS CLARIFICATION]`，能用合理默认定下的写进 Assumptions 并说明理由。

### III. 完整系统，非 MVP
- 本系统是「可上线的完整系统」，不是技术验证原型。
- 每个 Phase 结束 MUST 可独立上线（P1 核心闭环 / P2 引擎强化 / P3 生产化）。
- 生产化要素（可观测、审计、备份、安全加固）MUST 在对应 Phase 落地，禁止「先上线后补」。
- 禁止以「反正是内部系统」为由跳过安全、成本控制或数据完整性设计。

### IV. 质量门禁（每个 Phase 必过）
- 实现 MUST 通过 lint + 类型检查 + 测试（pytest + vitest）。
- 关键路径（拖拽分配、任务状态机、执行引擎、成本熔断）MUST 有集成测试覆盖。
- 测试 MUST 覆盖：正常路径、边界条件、失败路径、并发/重试/幂等。
- 每次提交前 MUST 运行 `git status` 复查工作区，防止密钥/临时文件误纳入。

## 技术栈与架构约束

- 后端 MUST 使用 Python：FastAPI + SQLAlchemy 2.0 (async) + Alembic + Celery 5 + Redis。
- 前端 MUST 使用 React 18 + TypeScript + Vite + TailwindCSS + dnd-kit + TanStack Query + Zustand。
- 数据库 MUST 使用 PostgreSQL 16，缓存/队列 MUST 使用 Redis，附件/制品 MUST 使用 S3 兼容对象存储（MinIO）。
- LLM 调用 MUST 通过 LiteLLM 网关统一接入（多模型可切换、成本统计、重试）。
- Agent 执行 MUST 采用自研轻量编排器（状态机 + ReAct 工具循环），禁止引入 LangGraph 作为运行时依赖。
- 代码沙箱 MUST 使用 Docker 一次性容器（资源限额 + 超时 + 隔离），禁止在宿主直接执行任意用户代码。
- API 层 MUST 只负责入队与校验，重活 MUST 交给执行引擎，保证分配接口 RT < 100ms。
- 实时通信 MUST 使用 WebSocket 推送运行状态与分步日志。
- 部署 MUST 使用 Docker Compose + Caddy（HTTPS），环境配置走 `.env`，密钥不落镜像不入库。

## 合规与安全边界

- 认证 MUST 使用 JWT（access + refresh），RBAC 三角色：admin / member / viewer。
- 凭证（工具 API Key、MCP 配置）MUST 加密存储（Fernet），禁止明文落库、禁止打印到日志。
- 用户提交的任意脚本 MUST 只能运行在隔离沙箱中，禁止直接执行于宿主进程。
- 每个 Agent MUST 有成本上限，每个用户 MUST 有月配额，超限 MUST 自动熔断。
- 所有敏感操作（登录、Agent/组变更、工具使用、Run 操作、凭证修改）MUST 写入审计日志。
- 日志 MUST 脱敏（不打印 Authorization、密钥字段）。
- 所有数据库/存储访问 MUST 遵循资源归属校验（用户只能操作自己的资源）。

## 开发工作流与质量门禁

- 每个特性一个分支：`feat/<short-name>`，实现完成后合并。
- 提交信息规范：`spec` 阶段用 `docs(spec): NNN-<name> 功能规格（N 用户故事/N FR/N SC）`；实现阶段每个 Phase 一次提交，形如 `feat(<域>): <内容>（T0xx-T0yy）`。
- 实现偏离 spec/plan/contracts 时，MUST 回头改上游文档，并写进当次提交信息。
- 每个 Phase 结束时 MUST 更新 tasks.md 状态并如实记录（不留下「看起来按计划走了」的假象）。
- Constitution 变更 MUST 在 Sync Impact Report 记录版本变化、修改内容与未决项。

## Governance

- 本宪法优先于所有其他实践约定；与既有约定冲突时以本宪法为准。
- 所有 spec / plan / PR 评审 MUST 核对宪法合规性。
- 复杂度 MUST 被论证（Complexity Tracking 每条要能回答「为什么更简单的做法不行」）。
- Constitution 修订 MUST 更新版本号与日期，并记录在 Sync Impact Report。

**Version**: 1.0.0 | **Ratified**: 2026-11-14 | **Last Amended**: 2026-11-14
