# Quickstart: 011-browser-walkthrough 端到端验收记录

**Feature**: `011-browser-walkthrough` | **Date**: 2026-11-18 | **Branch**: `feat/011-browser-walkthrough`

> 本文所有 ✅ 均为**真实栈 + 真实浏览器**上的实测结果。§1 是验收设计，§4 待 P4 回填。

## 0. 环境

- 本地 compose 栈（`aw-*`）：`deploy/deploy.sh` 重建（011 零应用代码改动，
  但 dev extras 需要装在 backend venv：`HTTPS_PROXY=http://127.0.0.1:7897
  uv pip install --python backend/.venv/bin/python playwright`）
- 浏览器：本机缓存 `~/Library/Caches/ms-playwright/chromium_headless_shell-*`
  （脚本自动取最高版本；`S12_CHROMIUM_PATH` 可覆盖）
- 前端：脚本自拉起 `npm run dev`（端口 5199），或 `--frontend-url` 指向已有实例
- 邀请码：member + viewer（`TEST-VIEWER-CODE-456`）

## 1. 真实栈验收设计（`deploy/verify/s12_browser_walkthrough.py`）

| 组 | 项 | 对应 quickstart 清单 |
|---|---|---|
| A | A1 管理页 CRUD / A2 建需求选择器 / A3 项目下拉+URL 深链 / A4 卡片徽章 / A5 viewer 门 | 008 §2（5 项） |
| B | B1 依赖区块 / B2 blocked 提示 / B3 卡片前置徽章 / B4 错误横幅（拖拽） / B5 铃铛跳转 | 009 §3（5 项） |
| C | C1 成员区管理 / C2 成员只读 / C3 成员看板与表单 / C4 铃铛成员通知 / C5 viewer 门 | 010 §3（5 项） |

```bash
backend/.venv/bin/python deploy/verify/s12_browser_walkthrough.py \
  --base http://localhost:8080 --invite VERIFY-010-MEMBER \
  --viewer-invite TEST-VIEWER-CODE-456
```

输出格式与 s1–s11 一致：逐项 ✅/❌（带耗时）→ 汇总行
「合计 15 项：N 通过 / M 失败 / K 跳过」→ 退出码。

## 2. 门禁（合并前全量）

```bash
cd backend && DATABASE_URL="...agent_workbench_test" python -m pytest -q
python -m ruff check app tests && python -m ruff format --check . && python -m mypy app
# s12 两连跑（SC-004 可重复）
backend/.venv/bin/python deploy/verify/s12_browser_walkthrough.py --base ... --invite ...
backend/.venv/bin/python deploy/verify/s12_browser_walkthrough.py --base ... --invite ...
# s 系列回归（011 零应用改动，但跑一遍证明没碰坏）
backend/.venv/bin/python deploy/verify/s11_project_sharing.py --base ... --invite ...
```

既有基线：后端 1462 passed / 前端 137 passed / s8–s11 全绿。011 后只增不减
（应用代码零改动，基线数字不变）。

## 3. 反证实验（SC-005）

跑通后**人为破坏一项**（如临时把 RequestCard 的徽章文案改错）→ 重跑 s12
→ 该项 ❌ + 产物落盘 + 退出码非零 → 还原。证明验收不是永远 ✅ 的摆设。
记录：_待填_。

## 4. 实测记录（待 P4 填写）

| 日期 | 脚本/命令 | 结果 |
|---|---|---|
| _待填_ | s12 首跑（15 项） | _待填_ |
| _待填_ | s12 二跑（SC-004 可重复） | _待填_ |
| _待填_ | 墙钟时间（SC-003 <180s） | _待填_ |
| _待填_ | 反证实验（SC-005） | _待填_ |
| _待填_ | B4 拖拽 vs 退化 | _待填_ |
| _待填_ | s11 回归 | _待填_ |
| _待填_ | 三份 quickstart 回填情况 | _待填_ |
