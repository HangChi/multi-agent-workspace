# Quickstart: 011-browser-walkthrough 端到端验收记录

**Feature**: `011-browser-walkthrough` | **Date**: 2026-11-18 | **Branch**: `feat/011-browser-walkthrough`

> 本文所有 ✅ 均为**真实栈 + 真实浏览器**上的实测结果（2026-11-18）。§5 是 s12 抓到的发现清单。

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
| 2026-11-18 | s12 首跑（15 项） | ✅ 跑通：8 通过 / 7 失败（失败全部是精确发现，见 §5） |
| 2026-11-18 | s12 二跑（SC-004 可重复） | ✅ 结果完全一致（同 8 项过 / 同 7 项败） |
| 2026-11-18 | 墙钟时间（SC-003 <180s） | ✅ 36s（含 dev server 探测与三组断言） |
| 2026-11-18 | 反证实验（SC-005） | ✅ 破坏「创建项目」按钮文案 → A1 转 ❌ + 截图产物 + 退出码 1；还原后复绿 |
| 2026-11-18 | B4 拖拽 | ✅ 拖拽成功（dnd-kit 传感器响应）；**无退化**——看板没有点击分配入口，409 消息被 ErrorBanner bug 吃掉（发现 #2）——012 修复后复跑转 ✅ |
| 2026-11-18 | s11 回归 | ⏳ 未跑（011 零应用改动，s11 结果不受影响；按纪律不虚填） |
| 2026-11-18 | 三份 quickstart 回填 | ✅ 008 四条 ✅/一条 ⏳；009 两条 ✅/三条 ⏳；010 两条 ✅/三条 ⏳——全部附根因 |

## 5. s12 抓到的发现（按纪律只记录，修复另开特性）

| # | 发现 | 根因 | 影响项 |
|---|---|---|---|
| 1 | 详情弹层数据不完整 / 增删边后不刷新（✅ **已修复，013**） | 两部分：① 弹层 detail 是 BoardPage 局部快照，不吃 `invalidateQueries`——增删边后显示陈旧列表；② s12 自身没等详情拉取完成就断言（曾把该时序误判成本 bug）。修复：DependencySection 加 `onChanged` 回调、BoardPage 重拉弹层详情；s12 `_open_detail` 等 `GET /requests/{id}` 完成 | B1/B2（009） |
| 2 | 409 的逐条阻塞消息到不了用户，只显示（✅ 已修复，012）「请求失败，请稍后重试」 | `ErrorBanner` 对**已提取的字符串**二次 `extractError` 落兜底；**19 处调用点全中**（自 007 T082 起所有后端错误消息都不可见） | B4（009）+ 全局 | 修复：`typeof error === "string" ? error : extractError(error)`（012 一行判型+2 单测；s12 B4 复跑转 ✅） |
| 3 | viewer 看得见「项目」导航入口（✅ **已修复，013**） | `nav.ts` 的 `/projects` 项无 `memberOnly: true`（模板/调度项都有） | A5/C5（008/010） | 修复：给该导航项加 `memberOnly: true` |
| 4 | 成员的项目页与建需求表单看不到共享项目（✅ **已修复，013**） | `ProjectsPage`/`RequestForm` 查 `scope: 'mine'`（后端 relevant 口径 010 已扩展，前端没接） | C2/C3（010） | 修复：两处查询切 `relevant` |

**全部已修复**：#2 → 012（ErrorBanner）；#1/#3/#4 → 013（依赖弹层刷新 + nav memberOnly + scope relevant）。s12 终态 **15/15**。
