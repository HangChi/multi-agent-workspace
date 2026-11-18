# Quickstart: 008-project-entity 端到端验收记录

**Feature**: `008-project-entity` | **Date**: 2026-11-18 | **Branch**: `feat/008-project-entity`

> 本文所有 ✅ 均为**真实 docker compose 栈**上的实测结果。§1 是验收设计，§2 待 P5 回填。

## 0. 环境（与 s1–s8 同构）

- 本地 compose 栈（`aw-*`），API/Worker/Beat 用 `deploy/deploy.sh` 重建（含 008 代码）
- LLM：真实端点（`LLM_MODEL=Deepseek-V4.1-Flash`）；008 本身不调 LLM，
  但 s9 场景 D/E 复用 007 的分派-执行链路，需要真实执行
- 迁移：`alembic upgrade head` → `0011_request_dispatch` → **`0012_project_entity`**
- 邀请码：沿用 `HVFWEAMLS5K`（member）与 `VERIFY-MEMBER-A/B`（回归脚本）

## 1. 真实栈验收脚本设计（`deploy/verify/s9_project_container.py`）

| 场景 | 用例 | 断言 |
|---|---|---|
| A 建项目并归类 | P-1/US1 | 建 2 项目；3 条需求分别归属；详情与列表 `project_id/name` 正确；同名 409 |
| B 管理动作 | P-5~P-7/US2 | 改名后旧需求 name 同步；归档后新建 400 但既有链路不变；删除后需求 project 置空且需求/Run 计数不变 |
| C 筛选与深链 | US3 | `?project_id=` / `?project=none` / 与 scope 正交；列表计数与逐条 project_name 一致 |
| D 权限边界 | P-3/P-4/FR-005 | 他人项目 404；viewer 403；经手人改 project_id 403 但改标题 202 |
| E 回归 | SC-003/SC-006 | **s8 原样重跑仍 7/7**（在「需求有归属」的数据上）；s1–s4 29/29 |

脚本形态对齐 s8：`--base` 参数化，退出码非零即失败，输出逐场景 ✅/❌。

## 2. 前端走查（人工或浏览器脚本）

- [ ] `/projects` 管理页：列表（名称/描述/状态/需求数）+ 新建 + 行内改名 + 归档切换 + 删除确认（文案说明「需求会保留、归属 cleared」）
- [ ] 建需求表单出现项目选择器（默认「不归类」，仅列本人 active 项目）
- [ ] 看板工具条项目下拉（全部/各项目带计数/未归属），选中进 URL `?project=`，刷新保持
- [ ] 需求卡片显示项目徽章（无归属不渲染）
- [ ] viewer 登录：无项目下拉、`/projects` 无入口

## 3. 门禁（P5 合并前全量）

```bash
cd backend && DATABASE_URL="...agent_workbench_test" python -m pytest -q
python -m ruff check app tests && python -m ruff format --check . && python -m mypy app
cd frontend && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
backend/.venv/bin/python deploy/verify/s9_project_container.py --base http://localhost:8080 --invite <INVITE>
```

既有基线（008 前）：后端 1377 passed / 前端 123 passed / s8 7/7 / s1–s4 29/29。
008 后只增不减。

## 4. 实测记录（待 P5 填写）

| 日期 | 脚本/命令 | 结果 |
|---|---|---|
| _待填_ | s9_project_container.py 五场景 | _待填_ |
| _待填_ | 后端 pytest 全量 | _待填_ |
| _待填_ | 前端五件套 | _待填_ |
| _待填_ | s8 / s1–s4 回归 | _待填_ |
| _待填_ | 迁移 0012 空库 upgrade/downgrade | _待填_ |
