# Feature Specification: 前端 UI 全模块整改（015）

**Feature Branch**: `feat/015-ui-refresh`（已 ff 合并 master）

**Created**: 2026-11-19（实现完成后补记，见下方「补记说明」）

**Status**: Implemented

**Input**: 用户 2026-11-19 要求「逐个模块修改前端 UI，参考主流开源实现，
每个模块都要改（含设置页），表单、按钮等细节都要考虑，及时提交」。
随后三轮定点反馈驱动迭代：① 截图指出「分配目标区」与「页头范围切换」
两处；② 看到上线效果后反馈「UI 还是丑，继续优化」；③ 截图指出看板页
头像下拉被吸顶工具条盖住。

> **补记说明**：本特性实际开发时按用户要求「及时提交」，以 14 次模块提交
> 走完了实现与验收，但**没有先写规格**——`specs/015-*` 目录当时缺失，
> `.specify/feature.json` 仍指向 014。这份 spec / plan / research / tasks /
> quickstart 是 2026-11-19 傍晚补记的，内容按实际提交与实测结果反推，
> 不虚构当时不存在的决策过程。补记的原因与代价记在 research.md R-01。

## User Scenarios & Testing *(mandatory)*

### US1 — 逐模块 UI 整改（优先级 P1）

作为使用者，我希望工作台的每个页面都符合当代主流 SaaS 工具的观感与
操作习惯——不是「能用但廉价」，而是「愿意每天打开」。

**Acceptance Scenarios**：

1. **Given** 逐个业务模块（设计系统基座 / 布局导航 / 命令面板与通知 /
   UI 组件族 / 看板与分配 / 需求表单 / Agent / 模板市场 / Agent 组 /
   仪表盘 / 运行详情 / 调度 / 项目 / 管理后台），**When** 每模块完成，
   **Then** 单独一次提交，且提交前 `tsc` / `eslint` / `vitest` 三门禁通过。
2. **Given** 设置页及其子页（模板 / 调度 / 工具市场 / 管理后台），
   **When** 整改，**Then** 与主业务页同一套设计令牌，不出现「设置页是
   另一套系统」的割裂。
3. **Given** 键盘用户，**When** 只用 Tab / Enter / Esc 操作菜单、弹层、
   看板拖拽，**Then** 全部可达且焦点可见。

### US2 — 整改过程中暴露的数据正确性缺陷（优先级 P0）

作为使用者，我不接受「UI 变好看了，但表单会静默把 A 的数据存成 B」。
整改过程中发现四类**静默数据覆盖**，必须在同一特性内修掉。

**Acceptance Scenarios**：

1. **Given** Agent 页「从模板创建」已打开，**When** 再点某个 Agent 的
   「编辑」，**Then** 三态互斥（`showForm` / `editing` / `fromTemplate`），
   提交走编辑而非又建一个。
2. **Given** 连续点开两个 Agent（或组、调度规则）的编辑，**When** 第二个
   打开，**Then** 表单字段是第二个对象的（`key={editing?.id ?? 'new'}`
   强制重挂载），不残留 A 的输入。
3. **Given** 运行详情页正在看 run A 的日志，**When** 切到 run B，
   **Then** 日志清空后重拉，不把 A 的日志混在 B 顶部。
4. **Given** 看板处于 `scope=mine` 或带项目筛选，**When** 拖拽分配成功，
   **Then** 可见看板即时更新（按 query key 前缀快照做乐观更新，
   不硬编码某一个 key），且分配人邮箱补全。

### US3 — 截图级定点反馈闭环（优先级 P1）

作为使用者，我指出「这一处」时，希望改的是那一处，而不是被顺手扩大。

**Acceptance Scenarios**：

1. **Given** 截图指出分配目标区一排同色蓝方块，**When** 整改，
   **Then** 头像按 id 散列到多套柔和渐变，行内带图标、计数胶囊、
   虚线空位与过滤框。
2. **Given** 反馈「视觉还是丑」，**When** 提质，**Then** 从**层次**
   （明度差 + 阴影三层）与**品牌色**两处下手，而不是换一套色板了事。
3. **Given** 截图指出看板页头像下拉被盖住，**When** 修，
   **Then** 定位到层叠上下文（`z-header` 的父级把子元素封在 40 里），
   新增 `z-sticky(30)` 语义令牌，而不是把下拉的 z-index 再调大。

### Edge Cases

- **深浅色主题**：令牌化后两套主题都要可读；仅做冒烟级检查，无逐页对比。
- **Windows / Linux 快捷键**：`⌘K` 在非 mac 平台显示 `Ctrl+K`，
  通过 `utils/platform.ts`（`modCombo`）判定，不读 `navigator.platform` 硬编码。
- **jsdom 无 `matchMedia`**：`hasFinePointer()` 必须走防御性判断，
  否则引用它的组件一进单测就炸。
- **弹层叠加**：命令面板与 Modal 同时可能存在，Esc 只关栈顶。

## Requirements *(mandatory)*

- **FR-001**：整改 MUST 覆盖全部 14 个业务模块，每模块一次提交，
  提交前 `tsc --noEmit`、`eslint`、`vitest` 均通过。
- **FR-002**：MUST 提供可复用的弹层焦点基建 `hooks/useFocusTrap`
  （Tab 陷阱 / 焦点归还 / 滚动锁与滚动条补偿 / 弹层栈只让栈顶响应 Esc /
  `initialFocus` 参数），Modal 与 CommandPalette 共用同一实现。
- **FR-003**：Agent 配置表单 MUST 收敛为单一来源
  （`AgentConfigFields` + `useToolsCatalog` + `constants.ts`），
  AgentForm 与 TemplateForm 的重复配置区 MUST 消除（原 170 行）。
- **FR-004**：所有「编辑对象可切换」的表单组件 MUST 带
  `key={editing?.id ?? 'new'}`，禁止依赖 `useState` 初始化器重跑。
- **FR-005**：表单开关（新建 / 编辑 / 从模板创建）MUST 互斥，不得用多个
  独立布尔表达同一状态。
- **FR-006**：路由参数变化导致同组件复用时，派生 state（日志、草稿）
  MUST 清空。
- **FR-007**：乐观更新 MUST 按 `getQueriesData` 的 query key 前缀写快照，
  禁止硬编码单个 key。
- **FR-008**：看板拖拽 MUST 同时支持指针与键盘（dnd-kit 双传感器），
  且 `screenReaderInstructions`（怎么操作）与 `announcements`（过程播报）
  MUST 均为中文。
- **FR-009**：z-index MUST 走语义令牌（header / sticky / dropdown /
  palette / skip / modal / toast），页面内吸顶工具条 MUST 用 `z-sticky`，
  不得与 `z-header` 平级比较。
- **FR-010**：深色主题 MUST 通过 `ringOffsetColor DEFAULT=surface`
  消除焦点环白边；颜色 MUST 走令牌，禁止散落十六进制值。
- **FR-011**：视觉 MUST 具备三层阴影分工（card / lift / glow）与品牌表面
  令牌（`--surface-brand`），灰度层叠不得只靠 1px 边框区分层次。
- **FR-012**：组件测试 MUST 覆盖新基建（焦点陷阱、共享配置区、工具目录
  三态、ErrorBanner 直通）与受影响的页面行为，套件不得回退。

### Key Entities

- `hooks/useFocusTrap.ts`（新增）：弹层焦点管理单一实现。
- `components/agent/AgentConfigFields.tsx`、`hooks/useToolsCatalog.ts`、
  `constants.ts`（新增）：Agent/模板配置区的单一来源。
- `components/run/LogPanel.tsx`（新增）：日志面板（follow / 过滤 / 搜索 /
  复制 / ANSI 兜底）。
- `Button`（扩展）：`as="link"` 与 `tone="danger"`。
- `tailwind.config.js`（扩展）：`zIndex` 语义令牌 + `ringOffsetColor` 默认值。
- `utils/platform.ts`、`utils/validation.ts`（新增）：平台组合键判定、
  邮箱校验。
- **无后端改动**：本特性不新增接口、不改数据模型，故不产出
  `data-model.md` 与 `contracts/api.md`（对照 001–014 的九件套，
  这两份在本次为空集，不做占位空文件）。

## Success Criteria *(mandatory)*

- **SC-001**：17 次提交、69 个文件、+3814 / −1757 行，覆盖 14 个模块。
- **SC-002**：前端单测全程不回退：整改前 137 例，整改后 **143 例 / 26 文件**
  全通过（新增用例随模块提交进入）。
- **SC-003**：`npx tsc --noEmit`、`npm run lint`、`npm run build` 全部通过。
- **SC-004**：真实浏览器冒烟（member 账号 + vite dev 代理垫片）逐页无
  白屏、无控制台报错、拖拽与弹层可用。
- **SC-005**：四类静默数据覆盖缺陷全部修复，且有对应回归用例或人工复现记录。
- **SC-006**：上线后用户不再反馈「丑」类问题；三轮定点反馈逐条闭环。

## Assumptions

- 本特性范围是**表现层 + 交互层**，不新增业务能力、不动后端契约
  （唯一触及 `ErrorBanner` 的是 012 已完成的字符串直通，本次只消费）。
- 「参考主流开源实现」按 Linear / Vercel / GitHub 一类工具的通用语言理解：
  克制的品牌色、明确的层次、可预期的键盘行为——不复制具体视觉资产。
- 深色主题只做冒烟级保障，不承诺逐页对比度审计。
- 仓库仍无 Playwright e2e 常驻基建；本特性的浏览器验证是**一次性冒烟**，
  008/009/010 的走查清单回归复用 011 的 s12 脚本（见 quickstart §3）。
