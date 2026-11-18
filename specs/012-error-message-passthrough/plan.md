# Implementation Plan: 012 错误消息直通

**Date**: 2026-11-18 | **Branch**: `feat/012-error-message-passthrough` | **Spec**: `specs/012-error-message-passthrough/spec.md`

## Technical Context

- 唯一改动文件：`frontend/src/components/ui/ErrorBanner.tsx`（一行判型 + docstring）
- 测试：`frontend/src/components/ui/ErrorBanner.test.tsx`（5 → 7 例）
- 验证：前端五件套 + s12 复跑（B4 期望 ❌ → ✅）
- 新增依赖：零

## Constitution Check

| # | 宪法原则（v1.1.0） | 符合性 | 结论 |
|---|---|---|---|
| 1 | I. SDD+TDD | ✅ | 九件套先于代码；测试任务先于实现 |
| 2 | I. Red-Green-Refactor | ✅ | 先加「字符串直通」用例（红）→ 改组件（绿） |
| 3 | I. 测试未过不标记完成 | ✅ | s12 复跑 B4 转 ✅ 才回填 |
| 4 | II. spec 先行 | ✅ | 本目录九件套 |
| 5 | II. FR 有 Given/When/Then | ✅ | FR-001~005 挂 US 场景；E-1~E-4 用例表 |
| 6 | II. 范围显式「不含」 | ✅ | FR-005 + data-model §3 四条不做 |
| 7 | III. 完整系统 | ✅ | 组件+契约文档+单测+s12 验证+回填端到端 |
| 8 | IV. 质量门禁 | ✅ | 前端五件套 + s12 复跑 + 回归无新增失败 |
| 9 | 技术栈零新增 | ✅ | 零依赖 |
| 10 | 前端栈不破 | ✅ | 纯组件内改动 |
| 11-20 | 其余原则 | ✅ | 不涉及（纯前端显示层） |

**违例登记**：无。

## Phase 拆分（每 Phase 一次提交）

| Phase | 内容 | 任务 | 提交点 |
|---|---|---|---|
| P2 修复 | 测试先行（2 条新用例红）→ 组件一行修 + docstring（绿） | T001–T003 | `fix(ui): ErrorBanner 字符串直通` |
| P3 验证 | 前端五件套 + s12 复跑（B4 转 ✅）+ 回归零新增失败 | T004–T006 | `test(verify): s12 复跑 B4 转绿` |
| P4 回填合并 | 009 quickstart B4 ✅ + 011 quickstart 发现标记已修 + README/docs + ff 合并 | T007–T010 | 每项一次提交 |

## 风险与回滚

- **回归面**：唯一改动是组件内一行判型 + docstring。既有五条用例
  （错误对象路径）覆盖的行为不变；字符串路径此前就是坏的（显示兜底），
  没有「此前正确」的行为可破坏。
- **回滚**：`git revert` 一个提交即恢复原状（组件文件单文件改动）。
- **s12 复跑风险**：B4 依赖拖拽传感器——已验证在真实栈可用
  （011 的诊断日志 modal=1）；若复跑时传感器抖动，B4 的失败消息会
  明确区分「拖拽未触发」与「消息仍被吃」，不会误报。
