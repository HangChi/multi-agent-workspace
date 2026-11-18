# Feature 006 Implementation Plan: 运行自动重试

> **Branch**: `feat/006-run-retry` | **Date**: 2026-11-18 | **Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

## 1. Technical Context

- **语言/运行时**：Python 3.12+（`backend/`）
- **Web/ORM/迁移**：FastAPI + SQLAlchemy 2.0 async + Alembic
- **队列**：Celery 5 + Redis（既有 `execute_run` 任务）
- **前端**：React 18 + TypeScript + Vite + TanStack Query
- **测试**：pytest + pytest-asyncio（真实 PostgreSQL 16）+ vitest
- **性能约束**：API 层 RT < 100ms 不变；重试调度发生在 Worker 内，不占 API 预算
- **约束来源**：宪法「技术栈与架构约束」「质量门禁」

## 2. Constitution Check

| 宪法条款 | 本计划如何满足 |
|---|---|
| I. SDD + TDD 双驱动 | 本四件套先行；每个 Phase 先写测试（红）再实现（绿） |
| II. 规格先行，实现后置 | spec 只描述 WHAT/WHY；21 条 FR 均有 Given/When/Then 锚点 |
| III. 完整系统，非 MVP | 重试含可观测（审计 + WS + 详情展示），不留「后补」 |
| IV. 质量门禁 | lint + mypy + pytest + vitest；关键路径（状态机、成本熔断、幂等）有集成测试 |
| 技术栈约束 | 不引入新组件；复用 Redis 锁、Celery、既有状态机 |
| API 层只入队 | 重试调度在 Worker 内完成，API 层无新增同步逻辑 |
| LLM 走 LiteLLM | 失败分类基于 LiteLLM 既有异常类型，不绕过网关 |

**无违规项，不需要 Complexity Tracking。**

## 3. 设计概要

### 3.1 失败分类（纯函数，可单测）

新增 `app/orchestrator/retry_policy.py`：

- `classify_failure(exc) -> FailureKind`，`FailureKind` 为 `TRANSIENT` / `PERMANENT`
- 瞬时类白名单：连接类异常（`ConnectionError`、`TimeoutError`、`OSError` 的网络子类）、
  `LLMClientError` 中标记为网关/超时的、HTTP 5xx 语义的异常
- 其余（含未知异常）一律 `PERMANENT`（research R-05）
- 退避计算：`backoff_seconds(attempt, base, cap) -> float`，full jitter，
  `random.uniform(0, min(cap, base * 2 ** attempt))`

纯函数、无 IO，单测可直接覆盖全部分支。

### 3.2 数据模型变更

`runs` 表新增三列（迁移 `0010_run_retry_fields.py`）：

| 列 | 类型 | 说明 |
|---|---|---|
| `attempt` | int NOT NULL DEFAULT 1 | 当前是第几次尝试 |
| `next_retry_at` | timestamptz NULL | 下次重试时刻（NULL = 不在等待） |
| `retry_history` | jsonb NOT NULL DEFAULT '[]' | 历次失败原因 `[{"attempt":1,"error":"...","at":"..."}]` |

`run_status` 枚举新增 `retrying`（PostgreSQL enum，`ALTER TYPE ... ADD VALUE`）。

不新建表：一次 Run 的尝试次数有上限（默认 3），jsonb 足够；
建表会带来 JOIN 与孤儿清理问题，而这里没有查询「按尝试聚合」的需求。

### 3.3 状态机变更

```
queued → running → retrying → running → ... → completed / failed
                     ↑          |
                     └──────────┘  (重试到点)
retrying → failed   (次数耗尽)
```

- 新增 `RunStatus.RETRYING`
- `_TERMINAL` 不含 `retrying`（它是中间态）
- `running → retrying`、`retrying → running`、`retrying → failed` 入迁移表
- `failed → queued`（手动重试）保持不变，手动重试同样累计 `attempt`

### 3.4 执行链路变更（`app/tasks/execute_run.py`）

1. 消费时先 `transition(run, RUNNING)`（既有逻辑），把 `attempt` 计入上下文
2. 失败分类：瞬时类且 `attempt < max_attempts` →
   - 追加 `retry_history`、`next_retry_at = now + backoff(attempt)`
   - `transition(run, RETRYING)`
   - 发 WS 事件 `run_retrying`
   - `apply_async(args=[run_id], countdown=wait)` 投递新任务
   - **不释放锁**（锁由新任务的执行体持有前一直有效；见 3.5）
   - `return "retrying"`
3. 瞬时类但次数耗尽 → 终态 `failed`（保留 history）
4. 业务类 → 终态 `failed`（既有行为）
5. 重试到点的新任务消费时：校验 `run.status == retrying` 且 `next_retry_at <= now`，
   不满足则丢弃（research R-11 / FR-011）

### 3.5 不重复执行的三道防线

| 防线 | 机制 | 防什么 |
|---|---|---|
| Redis 锁 | `SET NX EX run:lock:{id}`，拿不到即 `return "busy"` | 同一时刻两个执行体 |
| 状态校验 | 重试任务消费时校验 `status == retrying` | 终态后延迟消息到点 |
| 锁 TTL > 执行时长 | `_LOCK_TTL_SECONDS` 覆盖单次执行 | 执行中锁过期导致第二个执行体进入 |

第三道是既有属性，本特性补测试固化它（FR-010 的验收场景 2）。

### 3.6 成本累计

- 重试路径**不触碰** `run.cost_micros` / `run.cost_cents`（research R-04）
- 手动重试端点改为只重置 `error` / `current_step`，不再重置成本
- 熔断判据不变（`run.cost_micros > limit × 1M`），因累计天然覆盖历次尝试

### 3.7 配置项

| 变量 | 默认 | 说明 |
|---|---|---|
| `RUN_MAX_ATTEMPTS` | `3` | 总尝试次数（含首次） |
| `RUN_RETRY_BASE_SECONDS` | `2` | 退避基准（秒） |
| `RUN_RETRY_CAP_SECONDS` | `60` | 退避封顶（秒） |

### 3.8 前端

- 运行详情页：状态徽章支持 `retrying`（文案「重试中」），
  展示「第 N/M 次 · 约 Xs 后重试」与历次失败原因列表
- 看板/列表卡片：`retrying` 与 `failed` 用不同色调，不混淆（US4 场景 2）
- WS 事件 `run_retrying` 驱动实时更新

## 4. Phase 划分

| Phase | 内容 | 门禁 |
|---|---|---|
| P1 规格 | 本四件套 + checklist | 人工评审 |
| P2 后端 | 迁移 + 状态机 + retry_policy + 执行链路 + 端点成本口径 | 单测 + 集成测试全绿；ruff/mypy |
| P3 前端 | 详情页重试呈现 + 状态徽章 + WS 事件 | vitest + lint + tsc + build |
| P4 验收 | 真实栈脚本 + 文档同步 + 合并 | 全量门禁 + 真实栈通过 |

## 5. 风险与缓解

| 风险 | 缓解 |
|---|---|
| enum 加值在事务内失败（PG 限制 `ADD VALUE` 不能在事务块内后用） | 迁移用 `ALTER TYPE ... ADD VALUE IF NOT EXISTS`，Alembic 事务外执行该语句 |
| 重试消息在 Run 终态后到点 | 状态校验防线（3.5 第二道） |
| 锁 TTL 到期导致重复执行 | 既有 TTL 已覆盖单次执行上限；P2 补集成测试固化 |
| 累计成本让老任务「更容易熔断」 | 这是 spec FR-014 的预期行为；在已知限制里明示 |
| reaper 误杀重试中 Run | reaper 查询加 `status != 'retrying'`（等价于不为 waiting） |

## 6. 交付物清单

- `backend/alembic/versions/0010_run_retry_fields.py`
- `backend/app/orchestrator/retry_policy.py`（新）
- `backend/app/orchestrator/state_machine.py`（改）
- `backend/app/tasks/execute_run.py`（改）
- `backend/app/api/v1/runs.py`（改：手动重试成本口径）
- `backend/app/core/config.py`（改：三个配置项）
- `backend/app/core/audit.py`（改：`run_auto_retry` 动作）
- 前端：`RunDetailPage` / 状态徽章 / WS 事件处理
- 测试：`tests/unit/test_retry_policy.py`、`tests/unit/test_state_machine.py`（补）、
  `tests/integration/test_run_retry.py`、前端对应测试
- 文档：README 已知限制、architecture 状态机图、data-model
