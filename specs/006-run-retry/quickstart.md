# Quickstart: 运行自动重试 — 端到端验收记录

**Feature**: `006-run-retry` | **Date**: 2026-11-18 | **Branch**: `feat/006-run-retry`

> 本文所有 ✅ 均为**真实 docker compose 栈**上的实测结果，不是预期值。

## 0. 环境

- 本地 compose 栈（`aw-*`），API/Worker/Beat 用 `deploy/deploy.sh` 重建（含 006 代码）
- LLM：`deploy/mock-llm` 网关（`--profile mock`），承载 `RETRY_FAIL*` 关键词故障注入
- 迁移：`alembic upgrade head` → `0009 → 0010_run_retry_fields`

## 1. 真实栈验收（`deploy/verify/s7_run_retry.py`）

```bash
docker compose -p aw -f deploy/docker-compose.yml --profile mock up -d llm-gateway
# deploy/.env 的 LLM_BASE_URL 临时指向 http://llm-gateway:4000
backend/.venv/bin/python deploy/verify/s7_run_retry.py \
  --base http://localhost:8080 --invite TEST-ADMIN-CODE-123 --mock-llm http://127.0.0.1:4000
```

**总计：✅ 4/4 全部通过**（2026-11-18 实测）

| # | 场景 | 对应 | 结果 |
|---|---|---|---|
| A | 网关抖 2 次后恢复，无人干预完成 | SC-001 | ✅ `completed`，`attempt=2`，历史 1 条，成本 2 分 |
| B | 一直失败 → 重试耗尽终态 | SC-001 / SC-005 | ✅ `failed`，`attempt=3`，历史 3 条，`next_retry_at=null` |
| C | 累计成本达到上限即熔断 | SC-003 | ✅ `成本超限（2 分 > 上限 1 分），已熔断终止` |
| D | 重试等待期呈现为 `retrying` | SC-004 | ✅ 轮询到 retrying 状态 |

### 1.1 两个实测发现（已固化进代码注释）

1. **LiteLLM 层自带 `num_retries=1`**，会在一次 run 级尝试内先消耗若干次网关失败。
   因此场景 A 只发生了 **1 次 run 级重试**就成功，而不是 2 次。验收断言因此
   **不断言 attempt 精确值**，只断言「至少重试一次、最终完成、不超 3 次」——
   写死 `attempt == 3` 会让测试依赖两层重试预算的交互，那是实现细节不是需求。
2. **mock LLM 的失败计数器跨请求累积**，重复跑脚本时第二轮预算已用完、
   直接成功，看起来像「重试机制没生效」。已加 `GET /__reset` 端点，
   脚本 `setup()` 里先重置再跑。

## 2. 数据库与幂等

| 项 | 结果 |
|---|---|
| 迁移 0010（开发库实跑） | ✅ 三列 + `retrying` 枚举值；`ALTER TYPE` 走 autocommit_block |
| 迁移后旧数据 | ✅ `attempt=1` / `next_retry_at=null` / `retry_history=[]`（server_default） |
| 并发触发同一次执行 | ✅ 锁占用时后到者 `busy`，一次都不执行 |
| 终态后延迟消息到点 | ✅ 丢弃，状态与成本不变 |
| reaper 不误杀重试中 | ✅ `retrying` 不在 `_ACTIVE_STATUSES` |

## 3. 质量门禁

| 门禁 | 结果 |
|---|---|
| `pytest`（后端全量） | **1287 passed**, 6 subtests |
| `ruff check` / `ruff format --check` | 全绿（250 files） |
| `mypy app` | ✅ no issues（123 source files） |
| 前端 `eslint` / `prettier` / `tsc` / `vitest` / `build` | 全绿；vitest 115 passed / 21 files |

## 4. 怎么重跑

```bash
# 1. 起 mock 网关（仅 mock profile，生产不起这个服务）
docker compose -p aw -f deploy/docker-compose.yml --profile mock up -d llm-gateway
# 2. deploy/.env 的 LLM_BASE_URL 临时改为 http://llm-gateway:4000，重建 api/worker/beat
#    （注意：跑 pytest 时 export 的 DATABASE_URL 会污染 compose 变量替换，
#     部署前用 env -u DATABASE_URL 清掉）
# 3. 验收
backend/.venv/bin/python deploy/verify/s7_run_retry.py --base http://localhost:8080 --invite <INVITE>
# 4. 全量门禁
cd backend && python -m pytest -q
cd ../frontend && npm run lint && npm test && npm run build
```

## 5. 已知限制（与 spec 范围边界一致）

- 业务类失败不自动重试（配额/成本/步数/超时/取消/策略拒绝），未知异常也不重试。
- 没有死信队列；重试耗尽即终态 `failed`，由用户决定是否手动重来。
- pipeline 父 Run 不自动重跑（子 Run 各自独立重试，父 Run 收敛规则不变）。
- 手动重试不受自动重试的 3 次上限约束（用户显式决定，不该被自动预算挡住）。
- 成本口径变更：手动重试从「重置为 0」改为「跨次累计」——看板数字会因此
  比 006 之前更高，但那才是真实支出。
