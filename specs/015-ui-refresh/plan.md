# Implementation Plan: 前端 UI 全模块整改（015）

**Feature**: `015-ui-refresh` | **Date**: 2026-11-19 | **Branch**: `feat/015-ui-refresh`

**Input**: [spec.md](./spec.md)

> 本文件是补记的技术方案记录，对应实际提交 bf174c4..e571f4f 的实现路径。

## 1. 技术上下文

| 项 | 值 |
|---|---|
| 前端 | React 18 + TypeScript + Vite + Tailwind CSS |
| 拖拽 | dnd-kit（既有；本特性补键盘传感器与中文播报） |
| 数据层 | TanStack Query（既有；本特性修正乐观更新的 key 处理） |
| 测试 | Vitest + Testing Library（jsdom），26 文件 / 143 例 |
| 后端 | **零改动**（无接口、无模型、无迁移） |
| 新依赖 | **无**（焦点陷阱、日志面板、平台判定均自研，理由见 research R-02） |

## 2. 分阶段实施（每个阶段一次提交 = 一个可回滚的最小单元）

| 阶段 | 模块 | 关键产出 |
|---|---|---|
| M1 | 设计系统基座 | z-index 语义令牌、`ringOffsetColor` 默认值、字阶收敛、对比度、死代码清理 |
| M2 | 布局 / 导航 / 认证页 | 单入口「设置」菜单、深链回跳、平台快捷键、404 页 |
| M3 | 命令面板 / 通知 | `useFocusTrap` 复用、combobox 语义、三态分离 |
| M4 | UI 组件族 | 菜单键盘导航、Toast 时长、确认框焦点、分页无障碍、`Button` 变体 |
| M5 | 看板 / 分配 | 键盘 DnD + 中文播报、错误态、乐观更新前缀快照 |
| M6 | 需求表单 / 附件 / 依赖 | 多文件上传与进度、搜索、移除二次确认 |
| M7 | Agent 页 | 三态互斥、`AgentConfigFields` 抽取、工具目录三态、`errorField` |
| M8 | 模板 / 市场 | 共享配置组件（消 170 行重复）、Modal 内错误态 |
| M9 | Agent 组页 | 编辑器 `key` 修复、表单互斥、本页筛选、令牌清理 |
| M10 | 仪表盘 | 图表无障碍、令牌化、常量单一来源 |
| M11 | 运行详情 | `LogPanel`（follow / 过滤 / 搜索 / 复制 / ANSI 兜底）、WS 状态机、取消确认 |
| M12 | 调度 | `ScheduleForm` key 修复、cron 预览联动、图例与无障碍 |
| M13 | 项目 / 管理后台 | 挂载加载态、分页、凭证 Modal 化、表单细节 |
| M14 | 验收与合并 | 冒烟收尾（焦点落位、表单聚焦、中文播报）→ ff 合并 master |

后续三轮定点迭代（同一分支、未另开阶段）：

| 迭代 | 触发 | 提交 |
|---|---|---|
| 015b 视觉提质 | 用户「还是丑」 | 89bb67b（15 文件 +322/−191） |
| 015c 定点两处 | 用户截图 | bce316c（3 文件 +84/−18） |
| 015d 层叠修复 | 用户截图 | e571f4f（2 文件 +10/−2） |

## 3. 关键技术决策（详见 research.md）

1. **焦点管理收敛为 `useFocusTrap`**，不引 `focus-trap-react`：需求里有
   「弹层栈只让栈顶响应 Esc」与「滚动条宽度补偿」，现成库要包两层再改行为。
2. **配置表单由「复制」转「组合」**：`AgentConfigFields` + `useToolsCatalog`
   + `constants.ts` 三件套，AgentForm 与 TemplateForm 共用。
3. **表单重挂载而非重置**：`key={editing?.id ?? 'new'}`，把「状态初始化」
   交给 React 的挂载语义，避免手写 `useEffect` 重置漏字段。
4. **乐观更新按 key 前缀快照**：`getQueriesData({ queryKey: ['requests'] })`
   逐条写回，兼容 `scope` 与项目筛选的任意组合。
5. **z-index 语义令牌**：`header(40) > sticky(30)`，从「谁大谁赢」改成
   「谁属于哪层」。
6. **视觉提质按「层次 + 品牌色」两轴**：明度差、三层阴影、`--surface-brand`，
   而不是换色板。

## 4. 宪法符合性（Constitution Check）

| 原则 | 判定 | 说明 |
|---|---|---|
| I 规格先行 | **偏离后补** | 实现先于规格（用户要求「及时提交」），规格于当日补记；偏离与纠正见 research R-01 |
| II 测试守护 | 符合 | 每模块提交前三门禁；套件 137 → 143，无回退 |
| III 契约稳定 | 符合 | 无后端契约变更 |
| IV 归属与可见性 | 不适用 | 未触及权限逻辑（乐观更新只改缓存） |
| V 可观测 | 符合 | 无新增埋点需求；错误态统一走 `ErrorBanner` |
| VI 部署可回滚 | 符合 | 每阶段独立提交；容器单替换可回退上一镜像 |
| VII 文档同步 | **偏离后补** | 实现期间未写 specs/015，README 也未同步；本次一并补 |

## 5. 验收门禁

- `npx tsc --noEmit` / `npm run lint` / `npm run format:check` / `npx vitest run` /
  `npm run build` 五关。
- 真实浏览器冒烟：member 账号登录，逐页检查白屏、控制台报错、拖拽与弹层。
- 008/009/010 的 15 项走查清单：复用 011 的 `s12_browser_walkthrough.py` 回归。
