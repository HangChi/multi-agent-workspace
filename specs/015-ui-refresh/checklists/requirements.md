# Requirements Checklist: 015 前端 UI 全模块整改

| 条目 | 状态 | 说明 |
|---|---|---|
| US 独立可测 | ✅ | US1（逐模块，提交粒度可查）US2（四类数据损坏，可复现）US3（截图定点，可对照） |
| FR 有锚点 | ✅ | FR-001~012 挂 US 场景；FR-004~007 挂 US2，FR-008~011 挂 US1/US3 |
| 边界成节 | ✅ | 深浅色 / 非 mac 快捷键 / jsdom 无 matchMedia / 弹层叠加 |
| 「不含」有依据 | ✅ | spec §Key Entities 末条：无后端改动 → 不产出 data-model / contracts |
| SC 可判定 | ✅ | SC-001 改动量实测 / SC-002 143 例 / SC-003 五门禁 / SC-004 冒烟 / SC-005 四类缺陷 / SC-006 s12 15/15 |

## 对齐

- [x] 焦点陷阱单一实现（FR-002）——Modal 与 CommandPalette 共用 `useFocusTrap`。
- [x] 配置区单一来源（FR-003）——`AgentConfigFields` + `useToolsCatalog` + `constants.ts`，消 170 行重复。
- [x] 表单重挂载（FR-004）——Agent / 组 / 调度三处 `key={editing?.id ?? 'new'}`。
- [x] 表单开关互斥（FR-005）——Agent 页三态收敛为单一状态机。
- [x] 乐观更新前缀快照（FR-007）——`getQueriesData({ queryKey: ['requests'] })`。
- [x] 键盘 DnD + 中文播报（FR-008）——双传感器 + `screenReaderInstructions` 与 `announcements` 都中文化。
- [x] z-index 语义令牌（FR-009）——`z-sticky(30)` 修看板盖 Topbar。
- [x] 深色焦点环（FR-010）——`ringOffsetColor DEFAULT=surface`。
- [x] 层次与品牌色（FR-011）——三层阴影 + `--surface-brand`。
- [x] 组件测试覆盖新基建（FR-012）——套件 137 → 143，无回退。

## Notes

1. **规格先行的偏离**（research R-01）：先实现后补记，代价是 s12 脚本漂移
   与 prettier 漂移两条都**没人提醒**。纠正写进了提交前门禁与 tasks 收尾项。
2. **「丑」的根因不是配色**（R-07）：是卡片/页面/边框三者明度差只有 5 且
   没有阴影分层——换色板救不了「所有东西一样平」。
3. **同色头像是一个隐性可用性问题**：一排七八个相同蓝渐变，视觉上无法
   区分「这是不同的人」。按 id 散列多套渐变后才成立。
4. **z-index 的层叠上下文陷阱**（R-06）：子元素数值再大也逃不出父级上下文，
   跨容器比较必须找「最近的有 z-index 的祖先」。
5. **走查脚本是资产也是负债**（R-08）：它跟着 UI 走，UI 改交互就必须同批
   改脚本，否则绿灯变红灯的原因会被误判成「UI 坏了」。
