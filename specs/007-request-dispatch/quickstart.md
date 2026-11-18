# Quickstart: 007-request-dispatch 端到端验收记录

**Feature**: `007-request-dispatch` | **Date**: 2026-11-18 | **Branch**: `feat/007-request-dispatch`

> 本文所有 ✅ 均为**真实 docker compose 栈**上的实测结果，不是预期值。
> 当前状态：**规格已定稿，实现未开始**——§1–§3 是验收设计，§4 实测记录待 P6 填写。

## 0. 环境（与 s1–s7 同构）

- 本地 compose 栈（`aw-*`），API/Worker/Beat 用 `deploy/deploy.sh` 重建（含 007 代码）
- LLM：`deploy/mock-llm` 网关（`--profile mock`），无需真实密钥
- 迁移：`alembic upgrade head` → `0010_run_retry_fields` → **`0011_request_dispatch`**
- 种子：`scripts/bootstrap.py` 建 admin；注册三个 member 账号
  （dispatch_owner / dispatch_worker / dispatch_other）供三方场景使用

## 1. 真实栈验收脚本设计（`deploy/verify/s8_request_dispatch.py`）

八个场景，全部对 contracts 行为用例表：

| 场景 | 用例 | 断言 |
|---|---|---|
| A 分派闭环 | A-1 | owner 建需求 → `assign(user)` → 202 pending；worker relevant 列表含该需求；未读 +1 |
| B 接收 | AC-1/AC-3 | worker accept → 202 accepted；owner 收到 `request_accepted` 通知；other accept → 404 |
| C 退回 | RJ-1/RJ-3 | worker reject（含原因）→ 202 created；assignee 清空；owner 收到含原因通知；空 reason → 422 |
| D 转交 | A-2 | pending 再 assign 给第三人 → 历史 dispatch+reassign 两条；旧接收人收到 reassigned 通知 |
| E 执行与配额 | A-8/SC-005 | worker 把 accepted 需求拖给 mock Agent → Run completed；**owner 配额消耗增加、worker 配额不变**（双边读数） |
| F 边界 | A-5/A-6/A-9/AC-4 | 非相关人员 404；running 后分派 409；未 accept 拖 agent 409；重复 accept 409 |
| G 并发 | A-10/AC-5 | 两个并发 accept 同一 pending → 恰一个 202 一个 409；历史仅一条 accept |
| H 回归 | FR-014 | 既有 s1–s4 + strategy 脚本原样重跑，全绿（证明 agent/group 链路零行为变更） |

脚本形态对齐 `s7_run_retry.py`：参数化 `--base`，退出码非零即失败，输出逐场景 ✅/❌。

## 2. 前端验收（人工或浏览器脚本）

- [ ] 看板右侧出现员工卡目标列（与 Agent/组并列三类落点）
- [ ] 需求卡拖到员工卡 → 确认弹窗（无策略步骤）→ 卡片移入「待接收」列
- [ ]  worker 账号登录：顶栏铃铛未读数 = 分派数；点开通知中心逐条可读、「全部已读」归零
- [ ] 需求详情展示分派信息（当前接收人）+ 经手时间线（dispatch/reassign/accept/reject）
- [ ] 「与我相关 / 我创建的」范围切换行为符合 contracts §2
- [ ] 深链 `/?request=<id>` 对分派来的需求可打开（归属谓词扩展后不 404）

## 3. 门禁（P6 合并前全量）

```bash
cd backend && DATABASE_URL="...agent_workbench_test" python -m pytest -q
python -m ruff check app tests && python -m ruff format --check . && python -m mypy app
cd frontend && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
backend/.venv/bin/python deploy/verify/s8_request_dispatch.py --base http://localhost:8080
```

既有基线（007 前）：后端 1287 passed / 前端 115 passed。007 后只增不减。

## 4. 实测记录（待 P6 填写）

| 日期 | 脚本/命令 | 结果 |
|---|---|---|
| _待填_ | s8_request_dispatch.py 八场景 | _待填_ |
| _待填_ | 后端 pytest 全量 | _待填_ |
| _待填_ | 前端五件套 | _待填_ |
| _待填_ | s1–s4/strategy 回归 | _待填_ |
