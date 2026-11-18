# Feature Specification: 错误消息直通（012）

**Feature Branch**: `feat/012-error-message-passthrough`

**Created**: 2026-11-18

**Status**: Draft

**Input**: 011 的 s12 浏览器走查首轮抓出的最高优先级发现：`ErrorBanner`
对**已提取的字符串**二次调用 `extractError`，必然落兜底文案——
自 007（T082）起，所有后端错误消息用户都看不到。用户 2026-11-18
决定另开本特性修。

## User Scenarios & Testing *(mandatory)*

### US1 — 后端错误消息直达用户（优先级 P1）

作为用户，我在任何操作失败时应该看到**具体的**错误消息
（「有 1 个前置需求未完成：X（created）」、「同名项目已存在」、
「用户已停用，不能添加为项目成员」……），而不是千篇一律的
「请求失败，请稍后重试」。

**Why this priority**：这是 007 以来全系统错误 UX 的系统性缺陷——
19 处调用点全中，所有 409/400/403 的业务消息都被兜底文案吃掉。
用户拿不到「为什么失败」，只能猜或找开发者。

**Independent Test**：任一已知会返回业务错误的操作（如同名建项目、
拖被挡住的需求分配）→ 横幅显示后端原文。

**Acceptance Scenarios**：

1. **Given** assign 被 009 门禁挡下（409 带逐条消息），**When**
   用户在分配确认模态点「确认分配」，**Then** 模态内错误横幅显示
   「有 N 个前置需求未完成：…」原文。
2. **Given** 建项目撞名（409），**When** 提交，**Then** 横幅显示
   同名错误的原文。
3. **Given** 给停用用户加成员（400），**When** 提交，**Then**
   成员区横幅显示「用户已停用，不能添加为项目成员」。
4. **Given** 调用方传**字面量字符串**（如 `统计加载失败`），**When**
   渲染，**Then** 显示该字面量本身（不再被二次提取成兜底）。

### US2 — 组件契约清晰且不回归（优先级 P1）

作为前端维护者，`ErrorBanner` 的入参契约应该被明确文档化并有测试
钉住：既接受**原始错误对象**（组件负责提取），也接受**已提取的
消息字符串**（直通，不再提取）。

**Why this priority**：契约含糊是这次 bug 的根因——组件的 docstring
只说了「统一经 extractError() 提取」，没说也收字符串；19 个调用方
按「state 里存字符串」的直觉用，就全中了坑。

**Independent Test**：`ErrorBanner.test.tsx` 对两种入参形态各有断言。

**Acceptance Scenarios**：

1. 传 axios 形状错误对象 → 渲染提取出的 message（既有用例保持）。
2. 传字符串 → 原样渲染（新增用例）。
3. 传无 response 的 Error → 兜底文案（既有用例保持）。
4. 传 null/undefined → 不渲染（既有用例保持）。

### Edge Cases

- **空字符串**：`error=''` → 不渲染（falsy 短路，与 null 同等对待）。
- **非字符串非错误的怪值**（如数字）：交 `extractError` 处理 → 兜底
  （不猜）。
- **字面量里恰好含 `{error:...}` 形状**：不可能——字符串就是字符串，
  直通分支不解析。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**：`ErrorBanner` MUST 在提取前判型：`typeof error === 'string'`
  时**原样使用**该字符串；否则走既有 `extractError(error)`。
- **FR-002**：组件 docstring MUST 明确两种受收入参形态（原始错误对象 /
  已提取消息字符串），并各给一个用法示例。
- **FR-003**：`ErrorBanner.test.tsx` MUST 新增「字符串直通」与
  「空字符串不渲染」用例；既有五条用例 MUST 全绿（零回归）。
- **FR-004**：修复 MUST 经 s12 复跑验证——B4（拖被挡住需求分配）
  MUST 从 ❌ 转 ✅（错误横幅显示逐条阻塞项原文）。
- **FR-005**：本特性 MUST NOT 改任何后端代码；MUST NOT 改 19 处调用方
  （它们传字符串是对的——修在组件一处，全部受益）；MUST NOT 动
  `extractError` 本身（client.test.ts 的四条用例是它的契约）。

### Key Entities

- **ErrorBanner**：`src/components/ui/ErrorBanner.tsx`——唯一改动文件
  （一行判型 + docstring）。
- **extractError**：`src/api/client.ts`——不动（其单测契约不变）。
- 不新增实体。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**：s12 的 B4 从 ❌ 转 ✅（实测复跑）。
- **SC-002**：19 处调用点全部受益——无需逐个验证（组件级修复的
  数学事实），但 s12 的 A/B/C 三组复跑**零新增失败**。
- **SC-003**：前端五件套全绿（lint/format/typecheck/test/build），
  `ErrorBanner.test.tsx` 用例数 5 → 7。
- **SC-004**：009 quickstart 的 B4 条目从 ⏳ 回填为 ✅（附 s12 复跑
  记录）；011 quickstart §5 的发现 #1 标记「已修复（012）」。

## Assumptions

- 组件级修复优于改 19 个调用方：一处判型覆盖全部形态，回归面最小
  （research R-01 有论证）。
- 调用方「先 extract 再入 state」的模式**保留**——它让 state 类型
  是 `string | null`（简单、可序列化），不是 bug；bug 在组件不认字符串。
- 字面量字符串入参（MarketplacePage/DashboardPage）同样受益，
  无需单独处理。
