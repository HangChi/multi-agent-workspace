# Quickstart: 009-request-dependency 端到端验收记录

**Feature**: `009-request-dependency` | **Date**: 2026-11-18 | **Branch**: `feat/009-request-dependency`

> 本文所有 ✅ 均为**真实 docker compose 栈**上的实测结果（2026-11-18，部署后一轮）。

## 0. 环境（与 s1–s9 同构）

- 本地 compose 栈（`aw-*`），API/Worker/Beat 用 `deploy/deploy.sh` 重建（含 009 代码）
- LLM：真实端点（`LLM_MODEL=Deepseek-V4.1-Flash`）；009 的 E 场景需要真实执行
- 迁移：`alembic upgrade head` → `0012_project_entity` → **`0013_request_dependency`**
- 邀请码：`VERIFY-008-MEMBER`（member，100 次）

## 1. 真实栈验收（`deploy/verify/s10_request_dependency.py`）

```bash
backend/.venv/bin/python deploy/verify/s10_request_dependency.py \
  --base http://localhost:8080 --invite VERIFY-009-MEMBER
```

**实测结果：4/4 通过**：

| 场景 | 结果 | 实测数据 |
|---|---|---|
| A 连边与成环 | ✅ | 加边 201（带标题状态）；自依赖 400；重复 409；直接/传递成环 409；删边 204 |
| B 硬门禁 | ✅ | 409 逐条列出（终态前置不出现）；取消一个后剩余阻塞收敛；user 落点不查 |
| C 解锁通知 | ✅ | 前置终态 → owner+worker 各一条 `dependency_ready`（payload 带 cancelled）；幂等不重发 |
| D 回归 | ✅ | s9 5/5、s8 7/7、s1–s4 29/29（strategy 3/3 另跑） |

> 解锁路径在 s10 走 cancelled（不依赖 LLM）；completed/failed 两态由
> 集成测试 `test_dependency_gate.py` 的三终态参数化用例覆盖。

## 2. 原验收设计（保留备查）

| 场景 | 用例 | 断言 |
|---|---|---|
| A 连边与成环 | D-1~D-6 | 加边 201（带标题/状态）；自依赖 400；重复 409；直接成环与传递成环 409；删边 204 |
| B 硬门禁 | US2/FR-004 | 未完成前置拖 agent → 409 且消息逐条；前置 completed/failed/cancelled 三种终态后 → 202 |
| C 解锁通知 | US3/FR-007/008 | A 终态 → B 的 owner 与 assignee 各收一条 `dependency_ready`（同人去重）；payload 带终态；重复收敛不重发 |
| D 调度与回归 | FR-009/SC-005/006 | 定时规则撞门禁 → 建单不分配（trigger=fired + reason、无 Run）；**s9/s8/s1–s4 原样重跑全绿** |

脚本形态对齐 s9：`--base` 参数化，退出码非零即失败，输出逐场景 ✅/❌。

## 2. 前端走查（人工或浏览器脚本）

- [ ] 详情弹层「前置需求」区块：边列表（标题+状态）、添加器、移除按钮——⏳ **失败（2026-11-18 s12（`deploy/verify/s12_browser_walkthrough.py`，8/15 通过））**：B1。**弹层永远显示「前置需求 0 个」**。根因：`BoardPage` 的 `detailSource = inList ?? deepLinkRequest` 优先用**列表项**（`dependencies=null`，列表端不填明细），只有请求不在看板第一页时才走深链取详情。连边/删边 API 全部正常（集成测试 9/9），坏的是弹层数据源。修复方向：弹层打开时按 id 取一次详情，或 `detailSource` 优先 `deepLinkRequest`。**未修（011 纪律）**
- [ ] 被挡住时区块顶部提示「有 N 个前置未完成，暂不能分配执行」——⏳ **失败（2026-11-18 s12（`deploy/verify/s12_browser_walkthrough.py`，8/15 通过））**：B2。提示文案出现了但数字是「有 **0** 个前置未完成」——与「被挡住」自相矛盾。根因同上（`blocked` 布尔来自列表项为 true，`unmet_count` 却取不到）。
- [x] 卡片前置徽章（未完成数量 + hover 标题），前置就绪后消失——✅ **2026-11-18 s12（`deploy/verify/s12_browser_walkthrough.py`，8/15 通过）**：B3（数量/hover/前置终态后消失全验；计数来自列表端 `unmet_count`，不受弹层 bug 影响）
- [x] 拖被挡住的需求给 Agent → 错误横幅逐条列出阻塞项——✅ **2026-11-18 s12（012 修复后复跑）**：B4 转绿——模态错误横幅逐条显示「有 N 个前置需求未完成：…」原文。**修复记录**：首轮失败根因是 `ErrorBanner` 对已提取字符串二次 `extractError` 落兜底（19 处调用点全中的系统性 bug，自 007 T082 起所有后端错误消息不可见），由 012（`feat/012-error-message-passthrough`）一行判型修掉；s12 的 B4 同步加「先搜索收窄看板」消除拖拽抖动
- [x] 铃铛收到「前置就绪」通知，点开跳到后续需求——✅ **2026-11-18 s12（`deploy/verify/s12_browser_walkthrough.py`，8/15 通过）**：B5（`dependency_ready` 通知 + 点击跳 `/?request=` 打开后续需求详情）

## 3. 门禁（P5 合并前全量）

```bash
cd backend && DATABASE_URL="...agent_workbench_test" python -m pytest -q
python -m ruff check app tests && python -m ruff format --check . && python -m mypy app
cd frontend && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
backend/.venv/bin/python deploy/verify/s10_request_dependency.py --base http://localhost:8080 --invite <INVITE>
```

既有基线（009 前）：后端 1403 passed / 前端 129 passed / s8 7/7 / s9 5/5 /
s1–s4 29/29 / strategy 3/3。009 后只增不减。

## 4. 实测记录（待 P5 填写）

| 日期 | 脚本/命令 | 结果 |
|---|---|---|
| _待填_ | s10_request_dependency.py 四场景 | _待填_ |
| _待填_ | 后端 pytest 全量 | _待填_ |
| _待填_ | 前端五件套 | _待填_ |
| _待填_ | s9 / s8 / s1–s4 / strategy 回归 | _待填_ |
| _待填_ | assign RT 计时（SC-003） | _待填_ |
| _待填_ | 迁移 0013 空库 upgrade/downgrade | _待填_ |
