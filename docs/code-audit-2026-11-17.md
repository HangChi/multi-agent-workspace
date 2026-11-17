# 代码审查报告（2026-11-17）

范围：`backend/`（117 个源文件 / 约 8.0k 语句）+ `frontend/`（tsc 全量）+ `deploy/`。
方法：先跑全量质量门禁，再人工审查关键路径（认证、Redis、对象存储、编排、部署），
每条结论都带文件行号，能从代码直接验证。

## 零、修复进度（分支 `fix/audit-2026-11-17`）

| # | 问题 | 状态 | 提交 |
|---|---|---|---|
| 1 | MinIO 镜像已不可拉取（部署阻断） | ✅ 已修 | `10072a0` |
| 2 | WS 凭证走 query（进 error_log） | ✅ 已修 | `3a6572f` |
| 3 | 无 CSP（+ 内联脚本阻止上 CSP） | ✅ 已修 | `34a7937` |
| 4 | Redis 每次操作新建连接 | ✅ 已修 | `e7ef9df` |
| 5 | Worker 每个事件新建事件循环 | ✅ 已修 | `50bb0d8` |
| 6 | MinIO 同步调用阻塞事件循环 | ✅ 已修 | `02cfa06` |
| 7 | LLM 调用无超时 / Celery 无任务时限 | ✅ 已修 | `d4bd0fd` |
| 8 | `App.tsx` render body 里调 init | ✅ 已修 | `04af068` |
| 9 | 每个写操作两次 commit | ⏸ 未修（需服务层去 commit，跨 10+ 文件） | — |
| 10 | 成本按「分」取整 | ⏸ 未修（需加字段 + 迁移，属数据模型变更） | — |
| 11 | 上下文无裁剪 | ✅ 已修 | `02c2f7f` |
| 12 | 运行时版本与 CI 不一致 | ✅ 部分修（镜像内导入冒烟） | `3ff7ca1` |
| 13a | 角色守卫重复定义 ×10 | ✅ 已修 | `77d3946` |
| 13b | 空壳 `marketplace_query_service` | ✅ 已修 | `cd74e90` |
| 13c | `reply` 未用 Pydantic | ✅ 已修 | `cd74e90` |
| 13d | `endpoints.ts` 1192 行单文件 | ⏸ 未修（纯拆分，收益低于改动风险） | — |
| 13e | `_cancel_descendants` 逐层查询 | ⏸ 未修（树深 <5，BFS 成本可忽略） | — |
| 13f | `revoke_all_refresh_tokens` SCAN+GET | ⏸ 未修（改 key 布局有漏撤销风险） | — |
| 13g | react-refresh 2 个 warning | ⏸ 未修（需拆 hook 文件，纯风格） | — |

**升级建议（未做）**：refresh token 改 `HttpOnly` cookie。它和 CSP 是同一件事的
两侧——CSP 降低 XSS 成功概率，httpOnly 降低 XSS 成功后的损失。本次只做了前者，
因为后者要动登录/刷新/前端存储三处，属于独立特性而非「修 bug」。

---

## 一、门禁现状（全部通过，作为基线）

| 项 | 结果 |
|---|---|
| `ruff check app tests` | All checks passed |
| `ruff format --check` | 222 files already formatted |
| `mypy app` | Success: no issues found in 117 source files |
| `pytest` | **1033 passed**，1 error（沙箱环境 `pytest-of-unknown` 目录冲突，与代码无关） |
| 覆盖率 | **81%**（7996 语句 / 1510 未覆盖，门禁 75%） |
| 前端 `tsc --noEmit` | 0 error |
| 前端 `eslint` | 0 error，2 个 `react-refresh/only-export-components` warning |
| 前端 `vitest` | 88 passed / 16 files |
| 前端 `vite build` | 成功，入口 309.82 kB（gzip 102 kB），已按路由分包 |

结论：**工程质量基线扎实**，问题不在「代码写错了」，而在少数架构点上的**性能与部署可靠性**，
以及若干一致性/安全细节。

---

## 二、P0：会直接挡住部署

### 1. 生产 compose 引用的 MinIO 镜像已从 Docker Hub 消失

- 位置：`deploy/docker-compose.yml:69` → `minio/minio:RELEASE.2023-03-20T20-16-18Z`
- 证据：本机实测 `docker pull minio/minio:RELEASE.2023-03-20T20-16-18Z`
  → `pull access denied for minio/minio, repository does not exist or may require 'docker login'`
- 你的 CI 注释（`deploy/verify/minio.Dockerfile:1`「Production compose is deliberately unchanged」、
  `ci.yml:150`）已经写明「旧公共镜像不再可拉取」，所以 CI 侧自建了镜像 —— 这是**已知的刻意选择**，
  不是疏忽。但刻意保留的代价需要明确：本机能 `up -d` 只是因为本地还留着 2023 年的旧层。
- 影响：任何一台全新机器 `docker compose up` 会卡在拉取 MinIO；换服务器/灾备恢复时直接失败。
- 建议：把 `deploy/verify/minio.Dockerfile` 提升为正式镜像（或改用可拉取的镜像源 + digest 固定），
  compose 改为 `build: ./minio` 或 `image: aw-minio:2025-09-07`；再在 CI 加一步
  `docker compose -f deploy/docker-compose.yml config` + 镜像可达性校验，避免「CI 绿、部署红」。

---

## 三、P1：安全

### 2. WebSocket 凭证走 query string，会落进 nginx error_log

- 位置：`backend/app/api/v1/ws.py:84`（`websocket.query_params.get("token")`）、
  `frontend/src/hooks/useRunSocket.ts:31`（`?token=`）
- 现状：`frontend/nginx.conf` 只是把 access_log 改成不记 query，但 error_log 格式内建、
  上游异常时仍会打印完整请求行（你们在注释里已承认）。网关/浏览器侧若再落一层日志，token 一样暴露。
- 建议：用 `Sec-WebSocket-Protocol` 子协议传票据（服务端 `accept(subprotocol=...)` 回显），
  或握手前先 `POST /ws/ticket` 换一次性 30s 票据。改动集中在这两个文件，无需改鉴权模型。

### 3. refresh token 存 localStorage + 全站无 CSP

- 位置：`frontend/src/stores/authStore.ts`（`persist` → `aw_tokens`）、`frontend/nginx.conf`（未设 CSP）
- 影响：任意 XSS 可读走 7 天有效的 refresh token，等于长期账号接管。
- 建议：refresh 换 `HttpOnly; Secure; SameSite=Lax` cookie（access 仍可放内存），
  或至少补 `Content-Security-Policy`（把内联主题脚本改成 nonce）。

---

## 四、P1：性能与稳定性（按预期收益排序）

### 4. 每次 Redis 操作都新建一条连接

- 位置：`app/core/events.py:25`、`app/core/token_store.py:36`、`app/core/login_limiter.py:34`
  —— 三个模块都是 `aioredis.from_url(...)` → 用完 `aclose()`。
- 代价（实测代码路径推算）：
  - 一次登录 = 3 次建连（`check_login_allowed` + `reset_login_failures` + `register_refresh_token`）；
  - 每条 run 事件 = 1 次建连，而事件是每步多条。
- 建议：模块级单例 `client = aioredis.from_url(..., max_connections=50)`，进程退出时关闭；
  三个模块共用一个 `app/core/redis.py`。

### 5. Worker 每发一个事件就 `asyncio.run()` 新建事件循环

- 位置：`app/tasks/events.py:129-140`
- 叠加放大：`sandbox_output` 按 4 KiB 分块（`events.py:68-82`），1 MB stdout ⇒ **256 个事件**
  ⇒ 256 个事件循环 + 256 条 Redis 连接，全部在关键路径上同步等待。
- 建议：Worker 是同步上下文，直接用 `redis.Redis` 同步单例发布（去掉 loop 与 async 桥接）；
  或维持一个常驻 loop + 一条持久连接。事件发布本身也可按 32 KiB 聚合。

### 6. MinIO 同步 SDK 在 async 端点里被直接调用（阻塞事件循环）

- 位置：
  - `app/api/v1/requests.py:131` → `presign_upload()` → `_ensure_bucket()`（一次 HEAD 请求）
  - `app/api/v1/requests.py:173` → `download_url()` → `_object_exists()` + `presign_get()`（2 次 HTTP）
  - `app/api/v1/runs.py:371` → `presign_get()`（1 次 HTTP）
- 影响：`minio.Minio` 是同步客户端，这几行在网络往返期间把整个事件循环钉住；API 只有 2 个 worker，
  并发下载附件时全站接口一起变慢。
- 建议：① `_ensure_bucket()` 用模块级 `_bucket_ready` 标志只做一次；
  ② 调用点统一 `await asyncio.to_thread(artifact_service.presign_get, key)`。

### 7. LLM 调用没有超时，run 级超时也拦不住它

- 位置：`app/executors/llm.py:113-121`（`litellm.acompletion(**kwargs)`，kwargs 里无 `timeout`）
- 影响：`runner.py:208` 的超时检查只发生在「每轮循环之前」。一次挂死的 LLM 请求会占住
  worker 槽位（`--concurrency=2`）到 SDK 默认超时，两个并发请求就能让 worker 停摆；
  Celery 侧也没配 `task_time_limit`，卡死任务不会被回收。
- 建议：`acompletion(..., timeout=90, num_retries=1)` + `asyncio.wait_for`；
  Celery 加 `task_time_limit`（略大于 `max_run_seconds`）与 `task_soft_time_limit`。

---

## 五、P2：正确性与一致性

### 8. `App.tsx` 在 render body 里调 `init()`

- 位置：`frontend/src/App.tsx:21` → `void useAuthStore.getState().init()`
- 问题：每次渲染都会执行，StrictMode 下启动即两次；`init()` 内部没有 `initialized` 幂等守卫，
  两条并发链路在 401 后会**同时**打 `/auth/refresh`，而 refresh 是一次性消费（30s 宽限）。
  两个新 token 对里最后落盘的可能是被另一次作废的那个 → 下次刷新被登出。
- 建议：移到 `useEffect(() => { void init() }, [])`，并在 `init()` 开头加 `if (get().initialized) return`。

### 9. 每个写操作两次 commit（业务一次 + 审计一次）

- 位置示例：`app/api/v1/requests.py:81` 与 `:91`；`agents.py` 8 处、`groups.py` 11 处同构。
- 影响：多一次网络往返（写接口 RT 直接 +30~50%），且出现「业务已提交、审计失败」的窗口——
  `write_audit` 失败只是 rollback 当前事务并记日志（`app/core/audit.py:59-71`），审计会静默丢。
- 建议：service 层不 `commit`，端点内 `write_audit` 之后统一一次 `await db.commit()`；
  关键操作传 `strict=True`。

### 10. 成本按「分」取整，小额调用被记成 0

- 位置：`app/executors/llm.py:57-68`（`round(cost_usd * 100)`）
- 影响：gpt-4o-mini 单次 1k prompt ≈ 0.015 分 → round 成 0。多步小调用累计下来成本被系统性低估，
  月配额与 `cost_limit_cents` 熔断都会偏松。
- 建议：内部按 micro-cent（或 `Decimal` 美元）累计，展示时再取整；DB 字段改成整数 micro-cent。

### 11. 上下文无裁剪，prompt 随步数单调膨胀

- 位置：`app/orchestrator/runner.py:180-186`（`messages` 只追加不裁剪）
- 影响：MCP 结果单条上限 64 KiB（`mcp_max_result_chars`），10 步就能堆出几十万 token，
  后面每一步都在为前面的工具输出重复付费，延迟同步上升。
- 建议：按 token 预算裁剪——保留 system + 原始需求 + 最近 N 轮，旧 tool 消息替换为摘要占位；
  复用 `_step_input()` 已有的脱敏思路。

### 12. 运行时版本与 CI 版本不一致

- `backend/Dockerfile` 用 `python:3.15-slim`，`frontend/Dockerfile` 用 `node:26-alpine`；
  CI 的测试作业跑的是 Python 3.12 / Node 22。
- 影响：真正跑的版本从未被单测覆盖，dependabot 一次 bump 就可能把「CI 绿」和「线上崩」分开。
- 建议：CI 增加 3.15 矩阵（至少跑 lint + 单测），或把 Dockerfile 与 CI 对齐到同一版本。

### 13. 结构性小债（可批量处理）

| 位置 | 问题 | 建议 |
|---|---|---|
| 8 个 `api/v1/*.py` | `MemberUser` / `AdminUser` 重复定义（如 `runs.py:41`、`groups.py:35`） | 收敛到 `api/deps.py` 导出 |
| `app/services/marketplace_query_service.py` | 纯转发空壳，0% 覆盖 | 删除，直接 import 原模块 |
| `frontend/src/api/endpoints.ts` | 1192 行 / 66 个导出，单文件 | 按域拆成 `api/auth.ts`、`api/runs.ts`…（re-export 保持兼容） |
| `app/api/v1/runs.py:225-250` | `_cancel_descendants` 逐层查询（BFS N+1） | 一条递归 CTE 取整棵子树 |
| `app/core/token_store.py:84-103` | `revoke_all_refresh_tokens` 走 SCAN + 逐 key GET | 改为按用户维护 jti 集合（SET） |
| `app/api/v1/runs.py:319` | `reply_run(payload: dict \| None)` 未用 Pydantic | 定义 `RunReplyIn(message: str)`，与其他端点一致 |
| `frontend/src/components/ui/ConfirmDialog.tsx:86`、`Toast.tsx:121` | react-refresh warning | 把常量/hook 拆到独立文件 |

---

## 六、建议的推进顺序

1. ✅ **已完成**：P0 镜像、LLM 超时、App init 竞态（+ Redis 单例、事件发布、
   MinIO 线程化、WS 子协议、CSP、上下文裁剪、角色守卫收敛）。
2. **下一迭代（需单独评估）**：
   - #9 写接口合并为单次提交（服务层去 `commit`，跨 10+ 文件，要先补事务边界测试）；
   - #10 成本精度（`cost_micros` 新字段 + 迁移 + 前端展示换算）；
   - refresh token 改 httpOnly cookie（与 CSP 配套，属独立特性）。
3. **顺手清**：#13d/#13e/#13f/#13g，可以合并成一个「一致性清理」PR。

## 七、复现命令

```bash
# 后端门禁
cd backend
./.venv/bin/python -m ruff check app tests && ./.venv/bin/python -m ruff format --check app tests
./.venv/bin/python -m mypy app
DATABASE_URL="postgresql+asyncpg://aw:<pwd>@localhost:5432/agent_workbench_test" \
  ./.venv/bin/python -m pytest -q --cov=app --cov-report=term-missing:skip-covered

# 前端门禁
cd frontend && npx tsc --noEmit && npx eslint src --ext .ts,.tsx && npx vitest run && npx vite build

# P0 复验
docker pull minio/minio:RELEASE.2023-03-20T20-16-18Z   # → pull access denied
```

> 说明：`pytest` 需要把 `DATABASE_URL` 指向 `agent_workbench_test`，并保证口令与本地
> `aw-postgres` 容器一致（本次容器实际口令是 `test-password-123`，与 `conftest.py` 里
> `aw-dev-password` 的默认值不同，直接用默认值会 1034 个 fixture error）。
