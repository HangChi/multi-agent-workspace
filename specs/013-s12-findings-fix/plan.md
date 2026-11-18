# Implementation Plan: 013 s12 发现修复

**Date**: 2026-11-18 | **Branch**: `feat/013-s12-findings-fix`

## Technical Context

- 改动：4 个前端文件 + 1 个验收脚本；零后端；零依赖。
- 验收：s12 复跑（目标 15/15）+ 前端五件套 + 后端全量（零改动验证）。

## Constitution Check（摘要）

SDD+TDD ✅（spec 先行；s12 断言是测试）；未实测不勾选 ✅（15/15 才回填）；
技术栈零新增 ✅；前端栈不破 ✅；运行时不变 ✅（纯前端显示层+导航门）。
违例：无。

## Phase

| Phase | 内容 | 提交 |
|---|---|---|
| P2 修复 | nav memberOnly / scope×2 / onChanged 回调 / s12 时序 | `fix(ui): s12 挂账三项发现全修` |
| P3 验证 | 部署 + s12 15/15（二跑）+ 五件套 + 后端全量 | 同 P2 提交内记录 |
| P4 回填合并 | 三份 quickstart + 011 §5 + README/docs + ff 合并 | 三次提交 |

## 风险

- **relevant 后的项目页**：owner 写操作门与 scope 正交（MemberSection
  isOwner），s12 的 C1（owner 管理）与 C2（成员只读）双向覆盖。
- **弹层重拉失败**：refreshDetail catch 后保持原快照（既有降级），
  不弹错（spec Edge Cases）。
- **回滚**：纯前端单特性，revert 一个提交即恢复。
