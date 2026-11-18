# Tasks: 012-error-message-passthrough

> Phase 拆分见 `plan.md`。测试先行（红→绿）；未实测不勾选。

## Phase 2 — 修复（提交点：`fix(ui): ErrorBanner 字符串直通`）

- [X] T001 [P] `ErrorBanner.test.tsx` 新增两条用例（先红）：字符串原样渲染 / 空字符串不渲染
- [X] T002 `ErrorBanner.tsx`：`typeof error === 'string' ? error : extractError(error)` + docstring 写明两种入参形态（各给示例）
- [X] T003 跑 T001 转绿 + 既有五条全绿（vitest ErrorBanner.test.tsx 7 例）

## Phase 3 — 验证（提交点：`test(verify): s12 复跑 B4 转绿`）

- [X] T004 前端五件套全绿（lint/format:check/typecheck/test/build）
- [X] T005 s12 复跑：B4 ❌ → ✅；记录输出
- [X] T006 s12 三组回归：其余 14 项零新增失败（仍是 6 项已知发现，B4 移除）

## Phase 4 — 回填与合并（每项一次提交）

- [X] T007 009 quickstart 的 B4 条目 ⏳ → ✅（附 s12 复跑记录与日期）
- [X] T008 011 quickstart §5 发现 #1 标记「已修复（012）」+ 012 quickstart 实测记录回填
- [X] T009 README/docs/architecture 同步（012 特性行 + ErrorBanner 契约说明）
- [X] T010 ff 合并 master 删分支 + 更新 MEMORY

## Notes

1. **不动 `extractError`**（client.test.ts 四条用例是它的契约）。
2. **不动 19 个调用方**——它们传字符串是合理模式，修在组件一处
   （R-01）。
3. **空串按无错误**（R-03）——不把「没有消息」谎报成「请求失败」。
4. s12 复跑若拖拽传感器抖动，B4 的失败消息会区分「拖拽未触发」与
   「消息仍被吃」——不误报。
