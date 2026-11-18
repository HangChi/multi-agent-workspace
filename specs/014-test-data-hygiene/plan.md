# Implementation Plan: 014 测试数据卫生

**Date**: 2026-11-18 | **Branch**: `feat/014-test-data-hygiene`

## Technical Context

- 新增：`deploy/verify/cleanup_test_data.py`（asyncpg，已随 backend venv）
- 重写：`frontend/src/components/board/AssignTargets.tsx` + 新增组件测试
- 零后端改动、零新依赖、零 schema 变更

## Constitution Check（摘要）

SDD+TDD ✅（spec 先行；组件测试 4 例）；未实测不勾选 ✅（清理与 s12
均实测）；技术栈零新增 ✅；运行时不变 ✅（纯前端 + 运维脚本）。
违例：无。

## Phase

| Phase | 内容 | 提交 |
|---|---|---|
| P2 实现 | 清理脚本（三版迭代到「传播+拓扑」）+ 面板重设计 + 组件测试 | `chore(verify)+refactor(ui)` |
| P3 验证 | pg_dump 备份 → 实测清理（303→1）→ s12 15/15 → 五件套 143 | 同 P2 |
| P4 收尾 | spec 八件套 + README/docs + ff 合并 | 各一次提交 |

## 风险与回滚

- **删错数据**：dry-run 默认 + 保护名单 + 单事务回滚 + 删前全库备份
  （`deploy/backup/pre-cleanup-*.sql`，已实测留存）。
- **面板重设计破坏拖拽**：title/id 契约逐字保留，s12 的 B4 复跑守门
  （实测 15/15）。
- **回滚**：前端 revert 一个提交；数据层用备份还原。
