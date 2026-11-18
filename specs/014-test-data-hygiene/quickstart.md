# Quickstart: 014-test-data-hygiene 端到端验收记录

**Feature**: `014-test-data-hygiene` | **Date**: 2026-11-18

> 本文所有 ✅ 均为实测结果。

## 0. 背景

用户截图：看板「拖到目标」面板 303 个条目——302 个是 s1–s12 验收脚本
攒的测试账号（每跑注册新用户、从不清理）。真实账号只有一个 admin。

## 1. 清理（实测 2026-11-18）

```bash
# 备份（FR-008）
docker exec aw-postgres pg_dump -U aw -d agent_workbench > deploy/backup/pre-cleanup-<ts>.sql
# dry-run 看不删
backend/.venv/bin/python deploy/verify/cleanup_test_data.py
# 真删
backend/.venv/bin/python deploy/verify/cleanup_test_data.py --execute
```

| 检查 | 结果 |
|---|---|
| 备份 | ✅ deploy/backup/pre-cleanup-20261118-235342.sql（1.35MB） |
| dry-run | ✅ 打印 302 账号 + 13 张表 4210 行，不动数据 |
| 执行 | ✅ 303 → 1 用户（owner@example.com）；requests/projects/agents/runs/group_members 全 0 |
| 保留数据 | ✅ admin 的 3 条审计日志完好 |
| FK 完整性 | ✅ 单事务，中途失败两次均完整回滚（零损失） |

## 2. 面板重设计（实测）

| 检查 | 结果 |
|---|---|
| 三行分区 + 计数 | ✅ 截图确认（同事 N / Agent N / 组 N） |
| 同事过滤框 | ✅ assignTargets.test.tsx 第 3 例 |
| 超 8 折叠 | ✅ 第 2 例（12 → 8 +「+4 展开」） |
| 空态引导 | ✅ 第 4 例 |
| 拖拽契约 | ✅ s12 复跑 15/15（B4 依赖 title） |
| 前端五件套 | ✅ lint/format/typecheck/143 passed/build |

## 3. 已知限制

- 验收脚本仍会建测试账号（增量）——UI 的过滤/折叠兜住；自动清理
  （脚本跑完自清）留作后续特性。
- 清理工具的 DSN 硬编码本地测试库——刻意如此（本地运维工具，不连生产）。
