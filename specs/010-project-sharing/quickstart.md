# Quickstart: 010-project-sharing 端到端验收记录

**Feature**: `010-project-sharing` | **Date**: 2026-11-18 | **Branch**: `feat/010-project-sharing`

> 本文所有 ✅ 均为**真实 docker compose 栈**上的实测结果。§1 是验收设计，§3 待 P5 回填。

## 0. 环境（与 s1–s10 同构）

- 本地 compose 栈（`aw-*`），API/Worker/Beat 用 `deploy/deploy.sh` 重建（含 010 代码）
- LLM：真实端点（`LLM_MODEL=Deepseek-V4.1-Flash`）；010 的场景 D 需要真实执行
- 迁移：`alembic upgrade head` → `0013_request_dependency` → **`0014_project_member`**
- 邀请码：`VERIFY-009-MEMBER`（member，100 次）

## 1. 真实栈验收脚本设计（`deploy/verify/s11_project_sharing.py`）

| 场景 | 用例 | 断言 |
|---|---|---|
| A 成员管理 | M-1~M-8 | 加成员 201 + 被加者通知；重复 409；停用 400；成员加人 403；自移除 409 |
| B 可见性 | US2/FR-004/005 | 成员立即（同会话）看到项目与需求；被移除后立即 404 |
| C 协作链 | US3/FR-006/SC-002 | 成员建→改→分配（202，009 门禁照查）→连边；改归属 403；accept 仍仅 assignee |
| D 回归 | SC-004/006 | s10 4/4、s9 5/5、s8 7/7、s1–s4 29/29 原样重跑；相关端点 RT 计时 |

脚本形态对齐 s10：`--base` 参数化，退出码非零即失败，输出逐场景 ✅/❌。

## 2. 前端走查（人工或浏览器脚本）

- [ ] 项目管理页成员区：owner 徽章 + 成员列表 + 添加器（users/options 数据源）+ 移除
- [ ] 成员登录：项目页显示「共享的」徽章，成员列表只读
- [ ] 成员看板：项目下拉含共享项目；建需求表单可选共享项目
- [ ] 铃铛收到「你已被加入/移出项目 X」通知
- [ ] viewer：无项目页入口、无成员操作入口

## 3. 门禁（P5 合并前全量）

```bash
cd backend && DATABASE_URL="...agent_workbench_test" python -m pytest -q
python -m ruff check app tests && python -m ruff format --check . && python -m mypy app
cd frontend && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
backend/.venv/bin/python deploy/verify/s11_project_sharing.py --base http://localhost:8080 --invite <INVITE>
```

既有基线（010 前）：后端 1439 passed / 前端 133 passed / s8 7/7 / s9 5/5 /
s10 4/4 / s1–s4 29/29 / strategy 3/3。010 后只增不减。

## 4. 实测记录（待 P5 填写）

| 日期 | 脚本/命令 | 结果 |
|---|---|---|
| _待填_ | s11_project_sharing.py 四场景 | _待填_ |
| _待填_ | 后端 pytest 全量 | _待填_ |
| _待填_ | 前端五件套 | _待填_ |
| _待填_ | s10 / s9 / s8 / s1–s4 回归 | _待填_ |
| _待填_ | 相关端点 RT 计时（SC-006） | _待填_ |
| _待填_ | 迁移 0014 空库 upgrade/downgrade | _待填_ |
