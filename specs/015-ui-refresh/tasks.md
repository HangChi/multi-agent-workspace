# Tasks: 015-ui-refresh

> 勾选口径：本文件为 2026-11-19 傍晚补记，所有 `[X]` 均对应**已合并的提交**
> 与**已实测的验证**；未实测项不勾（宪法纪律）。

## 规格补记

- [X] T001 补记 spec / plan / research / tasks / quickstart（含偏离与代价，R-01）

## 模块整改（每项一次提交，提交前 tsc / eslint / vitest 三门禁）

- [X] T002 设计系统基座：z-index 语义令牌、`ringOffsetColor` 默认值、字阶收敛、对比度、死代码清理（41ae8cb）
- [X] T003 布局 / 导航 / 认证页：单入口设置菜单、深链回跳、平台快捷键、404（2afe1ee）
- [X] T004 命令面板 / 通知：`useFocusTrap` 复用、combobox 语义、三态分离（b00c55c）
- [X] T005 UI 组件族：菜单键盘导航、Toast 时长、确认框焦点、分页无障碍、`Button` 变体（cf648e9）
- [X] T006 看板 / 分配：键盘 DnD + 中文播报、错误态、乐观更新前缀快照（0917a7e）
- [X] T007 需求表单 / 附件 / 依赖：多文件上传与进度、搜索、移除二次确认（b281069）
- [X] T008 Agent 页：三态互斥、`AgentConfigFields` 抽取、工具目录三态、`errorField`（f5793cd）
- [X] T009 模板 / 市场：共享配置组件（消 170 行重复）、Modal 内错误态（32778f9）
- [X] T010 Agent 组页：编辑器 `key` 修复、表单互斥、本页筛选、令牌清理（75f1979）
- [X] T011 仪表盘：图表无障碍、令牌化、常量单一来源（2c79881）
- [X] T012 运行详情：`LogPanel`（follow / 过滤 / 搜索 / 复制 / ANSI 兜底）、WS 状态机、取消确认（edc2ec5）
- [X] T013 调度：`ScheduleForm` key 修复、cron 预览联动、图例与无障碍（216c764）
- [X] T014 项目 / 管理后台：挂载加载态、分页、凭证 Modal 化、表单细节（067a3d1）

## 验收收尾与定点迭代

- [X] T015 冒烟收尾：面板焦点落输入框、表单聚焦、中文拖拽播报（2cdb509）
- [X] T016 视觉提质：明度差 + 三层阴影 + `--surface-brand`（89bb67b，用户反馈「还是丑」）
- [X] T017 分配目标区 / 页头范围切换定点重设计（bce316c，用户截图）
- [X] T018 看板吸顶工具条盖住 Topbar 下拉：`z-sticky` 令牌（e571f4f，用户截图）
- [X] T019 前端五门禁：`tsc --noEmit` / `lint` / `format:check` / `vitest`（143 例）/ `build`
- [X] T020 真实浏览器冒烟：member 账号逐页无白屏无报错，拖拽与弹层可用
- [X] T021 s12 复跑 008/009/010 的 15 项清单 **15/15**（脚本同步移除二次确认；本轮补跑）
- [X] T022 README / docs 同步 + `.specify/feature.json` 指向 015 + ff 合并 master 推 origin

## 后续（不在本特性范围）

- [X] T023 深色主题逐页对比度审计（`deploy/verify/dark_contrast_audit.py`，11 页 × 深浅两套主题实测 0 项低于 WCAG AA；顺带修 ink-muted/ink-faint 同值、危险色深色不达标）
- [ ] T024 视觉回归基线（截图对比）——若将来引入常驻 e2e 再做
