# Quickstart: 007-request-dispatch 端到端验收记录

**Feature**: `007-request-dispatch` | **Date**: 2026-11-18 | **Branch**: `feat/007-request-dispatch`

> 本文所有 ✅ 均为**真实 docker compose 栈**上的实测结果，不是预期值。

## 0. 环境（与 s1–s7 同构）

- 本地 compose 栈（`aw-*`），API/Worker/Beat 用 `deploy/deploy.sh` 重建（含 007 代码）
- LLM：真实端点 `https://token.ai-hf.cn/v1`（`LLM_MODEL=Deepseek-V4.1-Flash`）；
  验收脚本 `--model` 默认取该值，打 mock 网关时传 `gpt-4o-mini`
- 迁移：`alembic upgrade head` → `0010_run_retry_fields` → **`0011_request_dispatch`**
- 邀请码：`HVFWEAMLS5K`（member，10 次）；回归脚本另需
  `VERIFY-MEMBER-A/B` + `TEST-VIEWER-CODE-456`（已补建于本地库）

## 1. 真实栈验收（`deploy/verify/s8_request_dispatch.py`）

```bash
backend/.venv/bin/python deploy/verify/s8_request_dispatch.py \
  --base http://localhost:8080 --invite HVFWEAMLS5K
```

**实测结果：7/7 通过**（2026-11-18 17:2x，部署后一轮）：

| 场景 | 结果 | 实测数据 |
|---|---|---|
| A 分派闭环 | ✅ | `assign(user)` → 202 pending，**受理 15ms**（SC-001 <100ms）；worker relevant 列表含该需求；未读 0 → 1 |
| B 接收 | ✅ | accept → 202 accepted；owner 收到 `request_accepted`；陌生人 accept → 404 |
| C 退回 | ✅ | 空原因 → 422；附原因 reject → 202 created；owner 收到含原因通知 |
| D 转交 | ✅ | 二次 assign(user) → 202；历史 `['dispatch','reassign']`；旧接收人未读 +1 |
| E 执行与配额 | ✅ | 接收人拖给自己的 agent → Run **completed**；`run.owner_id == 创建者`；owner 收到 `run_completed` |
| F 边界 | ✅ | 陌生人 accept=404；未接收拖 agent=409；重复 accept=409；终态后再分派=409 |
| G 并发接收 | ✅ | 并发 accept → [202, 409]；历史 accept 恰 1 条 |

**回归（FR-014 零行为变更）**：`s1_s4_scenarios.py` 29/29 ✅；
`strategy_scenarios.py`（pipeline + router）3/3 ✅。

> 首轮跑 E 失败过一次：脚本建 Agent 用了 `gpt-4o-mini`，真实端点无此模型
> （Run 秒失败 `No available channel`）——006 换端点时踩过同款坑，
> 已给 s8/strategy 两个脚本加 `--model`（默认取 `LLM_MODEL`）并在文档注明。

## 2. 前端走查（人工或浏览器脚本）

- [x] 看板右侧员工卡目标列与 Agent/组分块展示（拖给人=转交，拖给执行者=启动）
- [x] 需求卡拖到员工卡 → 确认弹窗（无策略步骤 + 可选留言）→ 卡片移入「待接收」列
- [x] 顶栏铃铛未读数随分派增长；通知中心逐条可读、「全部已读」归零
- [x] 详情弹层展示当前接收人 + 经手时间线（谁/何时/为什么，邮箱显示）
- [x] 「与我相关 / 我创建的」范围切换符合 contracts §2
- [x] 深链 `/?request=<id>` 对分派来的需求可打开
- [ ] 真实浏览器端到端（playwright）未跑——仓库无 e2e 基建立项，
      以真实栈 HTTP 脚本 + 前端单测覆盖，浏览器人工走查见上（逐项 ✅）

## 3. 门禁（合并前全量）

| 门禁 | 结果 |
|---|---|
| 后端 pytest | **1377 passed**（基线 1287 + 90 新增；唯一 error 为沙箱测试既有环境问题 `pytest-of-*`，master 同现） |
| ruff check / format / mypy | 全绿 |
| 前端五件套 | eslint / prettier / tsc / vitest **123 passed**（基线 115 + 8）/ build 全绿 |
| s8 真实栈 | 7/7 |
| s1–s4 回归 | 29/29 |
| strategy 回归 | 3/3 |
| 迁移 0011 | 空库 upgrade/downgrade 双向验证（列/索引/FK/枚举 8 值） |
