# Quickstart: 008-project-entity 端到端验收记录

**Feature**: `008-project-entity` | **Date**: 2026-11-18 | **Branch**: `feat/008-project-entity`

> 本文所有 ✅ 均为**真实 docker compose 栈**上的实测结果（2026-11-18，部署后一轮）。

## 0. 环境（与 s1–s8 同构）

- 本地 compose 栈（`aw-*`），API/Worker/Beat 用 `deploy/deploy.sh` 重建（含 008 代码）
- LLM：真实端点（`LLM_MODEL=Deepseek-V4.1-Flash`）；008 本身不调 LLM，
  但 s9 场景 D/E 复用 007 的分派-执行链路，需要真实执行
- 迁移：`alembic upgrade head` → `0011_request_dispatch` → **`0012_project_entity`**
- 邀请码：沿用 `HVFWEAMLS5K`（member）与 `VERIFY-MEMBER-A/B`（回归脚本）

## 1. 真实栈验收（`deploy/verify/s9_project_container.py`）

```bash
backend/.venv/bin/python deploy/verify/s9_project_container.py \
  --base http://localhost:8080 --invite VERIFY-008-MEMBER --viewer-invite TEST-VIEWER-CODE-456
```

**实测结果：5/5 通过**：

| 场景 | 结果 | 实测数据 |
|---|---|---|
| A 建项目并归类 | ✅ | 建 2 项目；详情/列表 `project_id/name` 双写正确；同名 409；FR-010 计数 p1=2 |
| B 管理动作 | ✅ | 改名同步旧需求快照；归档后新建 400、既有归属不变；删除后需求保留且两列清空 |
| C 筛选与深链 | ✅ | `?project_id=`/`?project=none` 生效且与 scope 正交；worker 可见派给他需求所属的他人项目 |
| D 权限边界 | ✅ | 他人项目 404；viewer 403；经手人改 project_id 403 / 改标题 200 |
| E 回归 | ✅ | s8 7/7 + s1–s4 29/29（+strategy 3/3 另跑） |

**C 场景抓到的真 bug**（已修，见提交 `568aa4f`）：筛选下拉按「可见」给接收人
展示他人项目（FR-010 设计），但过滤校验却要求项目属于调用者——下拉给了个
点了就 404 的选项。修 `project_exists_for_filter` → `project_visible_to_user`
（名下 ∪ 含我可见需求），陌生人仍 404。**教训：下拉的可见性口径与过滤的
校验口径必须是同一个函数，否则一定有一侧点击后 404。**

## 2. 原验收设计（保留备查）

| 场景 | 用例 | 断言 |
|---|---|---|
| A 建项目并归类 | P-1/US1 | 建 2 项目；3 条需求分别归属；详情与列表 `project_id/name` 正确；同名 409 |
| B 管理动作 | P-5~P-7/US2 | 改名后旧需求 name 同步；归档后新建 400 但既有链路不变；删除后需求 project 置空且需求/Run 计数不变 |
| C 筛选与深链 | US3 | `?project_id=` / `?project=none` / 与 scope 正交；列表计数与逐条 project_name 一致 |
| D 权限边界 | P-3/P-4/FR-005 | 他人项目 404；viewer 403；经手人改 project_id 403 但改标题 202 |
| E 回归 | SC-003/SC-006 | **s8 原样重跑仍 7/7**（在「需求有归属」的数据上）；s1–s4 29/29 |

脚本形态对齐 s8：`--base` 参数化，退出码非零即失败，输出逐场景 ✅/❌。

## 2. 前端走查（人工或浏览器脚本）

- [x] `/projects` 管理页：列表（名称/描述/状态/需求数）+ 新建 + 行内改名 + 归档切换 + 删除确认（文案说明「需求会保留、归属 cleared」）——✅ **2026-11-18 s12（`deploy/verify/s12_browser_walkthrough.py`，8/15 通过）**：A1 全环通过（新建→改名→归档→删除确认含「保留」文案）
- [x] 建需求表单出现项目选择器（默认「不归类」，仅列本人 active 项目）——✅ **2026-11-18 s12（`deploy/verify/s12_browser_walkthrough.py`，8/15 通过）**：A2（默认不归类 + 仅本人 active 项目）
- [x] 看板工具条项目下拉（全部/各项目带计数/未归属），选中进 URL `?project=`，刷新保持——✅ **2026-11-18 s12（`deploy/verify/s12_browser_walkthrough.py`，8/15 通过）**：A3（URL 深链 + 刷新保持均验）
- [x] 需求卡片显示项目徽章（无归属不渲染）——✅ **2026-11-18 s12（`deploy/verify/s12_browser_walkthrough.py`，8/15 通过）**：A4（有归属渲染/无归属不渲染双向断言）
- [ ] viewer 登录：无项目下拉、`/projects` 无入口——⏳ **部分失败（2026-11-18 s12（`deploy/verify/s12_browser_walkthrough.py`，8/15 通过））**：看板无项目下拉 ✅，但**导航里看得见「项目」入口** ❌。根因：`nav.ts` 的 `/projects` 项没有 `memberOnly: true`（模板/调度项都有），viewer 点进去会吃到后端 403。修复：给该导航项加 `memberOnly: true`。**未修（011 零应用改动纪律，发现另开特性）**

## 4. 门禁（合并前全量）

```bash
cd backend && DATABASE_URL="...agent_workbench_test" python -m pytest -q
python -m ruff check app tests && python -m ruff format --check . && python -m mypy app
cd frontend && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
backend/.venv/bin/python deploy/verify/s9_project_container.py --base http://localhost:8080 --invite <INVITE>
```

既有基线（008 前）：后端 1377 passed / 前端 123 passed / s8 7/7 / s1–s4 29/29。
008 后只增不减。

## 5. 实测记录

| 日期 | 脚本/命令 | 结果 |
|---|---|---|
| 2026-11-18 | s9_project_container.py 五场景 | ✅ 5/5（含 E 回归） |
| 2026-11-18 | 后端 pytest 全量 | ✅ 1403 passed（基线 1377 + 26 新增） |
| 2026-11-18 | 前端五件套 | ✅ lint/format/tsc/129 passed/build 全绿 |
| 2026-11-18 | s8 / s1–s4 / strategy 回归 | ✅ 7/7 · 29/29 · 3/3 |
| 2026-11-18 | 迁移 0012 空库 upgrade/downgrade | ✅ 双向（列/唯一约束/索引核对，downgrade 后表消失） |
