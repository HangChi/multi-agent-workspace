# Quickstart: Agent 模板与一键克隆 — 端到端验收记录

**Feature**: `005-agent-templates` | **Date**: 2026-11-18 | **Branch**: `feat/005-agent-templates`

> 本文所有 ✅ 均为**真实 docker compose 栈**上的实测结果，不是预期值。
> 复现方式见文末「怎么重跑」。

## 0. 环境

- 本地 compose 栈（`aw-*`，PostgreSQL 16 + Redis 7 + MinIO + api/worker/beat/frontend/dns-proxy）
- 迁移：`alembic upgrade head` → `0008_run_cost_micros → 0009_agent_templates`
- 内置模板：`scripts/seed_agent_templates.py` 实跑一次（4 个），二次运行新增 0
- LLM 端点：`https://token.ai-hf.cn/v1`，`LLM_MODEL=Deepseek-V4.1-Flash`

## 1. 真实栈验收（`deploy/verify/s6_agent_templates.py`）

```bash
backend/.venv/bin/python deploy/verify/s6_agent_templates.py \
  --base http://localhost:8080 --invite TEST-ADMIN-CODE-123
```

**总计：✅ 14/14 全部通过**（2026-11-18 实测，全程 0.7s，均为同步轻量写，无需跑 Run）

| # | 场景 | 对应 | 结果 |
|---|---|---|---|
| S1-a | 从模板创建，配置逐字段一致 | US1 AS1 / SC-001 | ✅ 不一致字段=无 |
| S1-b | 基于模板微调，只有改动的字段不同 | US1 AS4 | ✅ 意外变动字段=无 |
| S2-a | 克隆，配置逐字段一致 + 名称派生 | US2 AS1 / SC-002 | ✅ 名称=「源 Agent xxxxxx 副本」 |
| S2-b | 克隆不复制实例级数据 | US2 AS2 / SC-003 | ✅ 组成员关系=0 |
| S2-c | 连续克隆三次名称互不冲突 | US2 AS3 | ✅ 副本 / 副本2 / 副本3 |
| S2-d | 克隆 restricted 源给出警告 | US2 AS4 / FR-012 | ✅ 文案与 `POST /agents` 逐字相同 |
| S3-a | 改 Agent 不影响模板 | US3 AS2 / FR-002 | ✅ |
| S3-b | 改模板不影响已创建的 Agent | US3 AS3 / SC-004 | ✅ |
| S4-a | 他人模板 404 且措辞与不存在一致 | US4 AS5 / FR-004 / SC-006 | ✅ body 完全相同 |
| S4-b | 他人不可改模板 | FR-004 | ✅ 404 |
| S4-c | 同名模板 409 且不自动加后缀 | US3 AS4 / FR-003 | ✅ |
| S4-d | 删模板不带走 Agent | US4 AS3 / FR-021 | ✅ 204 且 Agent 仍在 |
| S5-a | 内置模板存在且 ≥3 | US5 AS1 / SC-008 | ✅ 4 个 |
| S5-b | 内置模板只读 | US5 AS2 / FR-015 | ✅ 403「内置模板不可编辑」 |

## 2. 数据库层验证

| 项 | 结果 |
|---|---|
| 迁移往返（隔离 schema up → down） | ✅ 列/索引/FK 齐全，downgrade 后干净且保留 002 的 `agent_egress_mode` 枚举 |
| 部分唯一索引真的在库里 | ✅ `pg_indexes` 里带 `WHERE (owner_id IS NULL)` |
| NULL owner 同名被拒（R-05 回归销） | ✅ 抛 `uq_agent_templates_builtin_name` |
| 同一 owner 重名被拒 / 不同 owner 同名允许 | ✅ / ✅ |
| 删模板 / 删源 Agent → 产物保留且来源置 NULL | ✅ 双向 |
| 9 个 CHECK 约束（参数化） | ✅ 越界全被 DB 层拒（绕过了 Pydantic） |

## 3. 质量门禁

| 门禁 | 结果 |
|---|---|
| `ruff check app tests scripts` | ✅ All checks passed |
| `ruff format --check` | ✅ 247 files |
| `mypy app` | ✅ no issues（122 source files） |
| `pytest`（全量） | ✅ **1224 passed**, 6 subtests |
| 前端 `eslint` | ✅ 0 error |
| 前端 `prettier --check` | ✅ |
| 前端 `tsc --noEmit` | ✅ 0 error |
| 前端 `vitest` | ✅ **107 passed / 20 files** |
| 前端 `vite build` | ✅ `AgentTemplatesPage` 独立分包 11.19 kB（gzip 5.12 kB） |

> `pytest` 有 1 个**既有环境问题**（`tests/integration/test_mcp_fixture_http.py` 的
> `pytest-of-*` 目录冲突），在 `master` 上同样复现，与本特性无关，故在全量统计中排除。

## 4. 手动验证过的交互

- Agent 页「从模板创建」→ picker 选模板 → 表单预填（含提示词/工具/参数）→ 直接提交
- Agent 卡片「克隆」→ toast 提示「已克隆为 X 副本」→ 列表出现新卡片，带「克隆而来」角标
- Agent 卡片「存为模板」→ 弹层问名称 → 保存后 `/agent-templates` 出现该模板
- `/agent-templates` 页：内置区只读（无编辑/删除按钮）、我的模板区可编辑/删除/用模板创建
- viewer 打开 `/agent-templates` → 页内 403 提示，不发任何请求

## 5. 怎么重跑

```bash
# 1. 起栈 + 迁移 + 内置模板
docker compose -p aw -f deploy/docker-compose.yml up -d --build
cd backend && .venv/bin/alembic upgrade head
.venv/bin/python scripts/seed_agent_templates.py

# 2. 真实栈验收（14 项）
cd .. && backend/.venv/bin/python deploy/verify/s6_agent_templates.py \
  --base http://localhost:8080 --invite <INVITE_CODE>

# 3. 全量门禁
cd backend && python -m pytest -q
cd ../frontend && npm run lint && npm test && npm run build
```

## 6. 已知限制（与 spec 的 Scope Boundaries 一致）

- 模板是**个人私有**的，没有跨用户共享/模板市场/权限授予。
- 没有模板版本历史与差异对比；改坏了就重新另存。
- 克隆/模板创建**不复制**外部工具授权与出站规则（涉及凭证，需在产物上重新配置）。
- 没有模板导入导出与使用统计。
- 内置模板数量固定 4 个，由种子脚本注入；换端点后需重跑脚本更新模型。
