# Quickstart: 010-project-sharing 端到端验收记录

**Feature**: `010-project-sharing` | **Date**: 2026-11-18 | **Branch**: `feat/010-project-sharing`

> 本文所有 ✅ 均为**真实 docker compose 栈**上的实测结果（2026-11-18，部署后一轮）。

## 0. 环境（与 s1–s10 同构）

- 本地 compose 栈（`aw-*`），API/Worker/Beat 用 `deploy/deploy.sh` 重建（含 010 代码）
- LLM：真实端点（`LLM_MODEL=Deepseek-V4.1-Flash`）；010 的场景 D 需要真实执行
- 迁移：`alembic upgrade head` → `0013_request_dependency` → **`0014_project_member`**
- 邀请码：`VERIFY-009-MEMBER`（member，100 次）

## 1. 真实栈验收（`deploy/verify/s11_project_sharing.py`）

```bash
backend/.venv/bin/python deploy/verify/s11_project_sharing.py \
  --base http://localhost:8080 --invite VERIFY-010-MEMBER
```

**实测结果：4/4 通过**：

| 场景 | 结果 | 实测数据 |
|---|---|---|
| A 成员管理 | ✅ | 加人 201+被加者通知；重复 409；成员加人 403；陌生人 404；自移除 409；列表含 owner 标记 |
| B 可见性 | ✅ | 加人前不可见 → 加人后同会话立即可见（项目+需求）→ 移除后立即收紧 + 移出通知 |
| C 协作链 | ✅ | 成员建→改→分配（owner 的 agent，202）→连边；改归属 403 |
| D 回归 | ✅ | s10 4/4、s9 5/5、s8 7/7、s1–s4 29/29（strategy 3/3 另跑） |

## 2. 原验收设计（保留备查）

| 场景 | 用例 | 断言 |
|---|---|---|
| A 成员管理 | M-1~M-8 | 加成员 201 + 被加者通知；重复 409；停用 400；成员加人 403；自移除 409 |
| B 可见性 | US2/FR-004/005 | 成员立即（同会话）看到项目与需求；被移除后立即 404 |
| C 协作链 | US3/FR-006/SC-002 | 成员建→改→分配（202，009 门禁照查）→连边；改归属 403；accept 仍仅 assignee |
| D 回归 | SC-004/006 | s10 4/4、s9 5/5、s8 7/7、s1–s4 29/29 原样重跑；相关端点 RT 计时 |

脚本形态对齐 s10：`--base` 参数化，退出码非零即失败，输出逐场景 ✅/❌。

## 3. 前端走查（人工或浏览器脚本）

- [ ] 项目管理页成员区：owner 徽章 + 成员列表 + 添加器 + 移除（owner 才见操作）
- [ ] 成员登录：项目页显示「共享的」徽章，成员列表只读
- [ ] 成员看板：项目下拉含共享项目；建需求表单可选共享项目
- [ ] 铃铛收到「你已被加入/移出项目 X」通知
- [ ] viewer：无项目页入口、无成员操作入口

> 逐项 ⏳ 未做浏览器人工走查——仓库无 e2e 基建立项，以真实栈 HTTP 脚本
> （s11 四场景）+ 前端单测（projectMembers.test.tsx 4 例契约级）覆盖。

## 4. 门禁（P5 合并前全量）

```bash
cd backend && DATABASE_URL="...agent_workbench_test" python -m pytest -q
python -m ruff check app tests && python -m ruff format --check . && python -m mypy app
cd frontend && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
backend/.venv/bin/python deploy/verify/s11_project_sharing.py --base http://localhost:8080 --invite <INVITE>
```

既有基线（010 前）：后端 1454 passed / 前端 133 passed / s8 7/7 / s9 5/5 /
s10 4/4 / s1–s4 29/29 / strategy 3/3。010 后只增不减。

## 5. 实测记录

| 日期 | 脚本/命令 | 结果 |
|---|---|---|
| 2026-11-18 | s11_project_sharing.py 四场景 | ✅ 4/4（含 D 回归） |
| 2026-11-18 | 后端 pytest 全量 | ✅ 1462 passed（基线 1454 + 8 口径/成员） |
| 2026-11-18 | 前端五件套 | ✅ lint/format/tsc/137 passed/build 全绿 |
| 2026-11-18 | s10 / s9 / s8 / s1–s4 / strategy 回归 | ✅ 4/4 · 5/5 · 7/7 · 29/29 · 3/3 |
| 2026-11-18 | 相关端点 RT（SC-006） | ✅ s11 B/C 全程 <100ms（列表/详情均 0.0s 级） |
| 2026-11-18 | 迁移 0014 空库 upgrade/downgrade | ✅ 双向（复合 PK/索引核对 + 枚举加值，downgrade 后表消失） |
