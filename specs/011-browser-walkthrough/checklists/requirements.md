# Requirements Checklist: 011 浏览器走查验收

逐条带核对说明。**Notes** 记实测/决策记录。

## Spec 质量核对

| 条目 | 状态 | 说明 |
|---|---|---|
| 每个 US 独立可测 | ✅ | US1（基建+汇总）、US2/3/4（三组清单）各自可单独跑 |
| FR 有 Given/When/Then 锚点 | ✅ | FR-001~010 挂 US 场景；contracts §3 逐项编号可执行 |
| 边界单独成故事或成节 | ✅ | Edge Cases 覆盖端口占用/浏览器缺失/登录态/拖拽/多角色/后端未起 |
| 「不含」每项有依据 | ✅ | FR-009 + data-model §4 五条 |
| 未决事项 ≤3 且无 NEEDS CLARIFICATION | ✅ | 0 个；全部选择进 Assumptions |
| SC 可判定 | ✅ | SC-001 15/15 / SC-002 零漏记 / SC-003 墙钟 / SC-004 可重复 / SC-005 反证 |

## 与宪法（v1.1.0）的对齐核对

- [x] I. SDD+TDD：九件套先于脚本；「跑过」与「回填」绑定（FR-008）。
- [x] I. 未实测不勾选：跳过项（无 viewer 邀请码）保持 ⏳ 并注明。
- [x] II. 范围边界：e2e 框架/POM/Allure/DB 种子/应用改动 五项显式排除。
- [x] III. 完整系统：15 项 + 自管理 dev server + 产物 + 回填端到端。
- [x] IV. 质量门禁：P2/P3 每阶段跑 s12；P4 全回归。
- [x] 运行时零新增依赖（playwright 只在 dev extras，lock dev 组，Docker `--no-dev` 实测不装）。
- [x] 前端/后端应用代码零改动（R-08）。
- [x] 不碰执行引擎/Run/LLM（B4 只建 Run 不等执行）。

## 与既有契约的对齐核对

- [x] s1–s11 家族：同 CLI（--base/--invite）、同输出格式（✅/❌+汇总）、
  同退出码纪律、同随机后缀种子纪律。
- [x] 008/009/010 三份 quickstart 的走查清单：15 项逐条对应，编号
  A1–A5/B1–B5/C1–C5 与清单条目顺序一致（回填时可逐条对）。
- [x] 004 调度纪律的复用：dev server 自管理同 deploy.sh 的「一条命令」。
- [x] 007 通知契约：B5/C4 断言消费既有通知类型（dependency_ready/
  project_member_added），不改通知链路。

## Notes（实测/决策记录）

1. **形态一度改向 Node 后回归 Python**：沙箱直连 pythonhosted 502 导致
   Python 版 Playwright 装不上，曾准备改用本机已装的 Node 版；用户提示
   代理 7897 后安装成功（playwright 1.65.2），回归用户原本选择的
   Python 形态。**教训：环境受限时先问有没有代理，别急着改技术选型。**
2. **浏览器用缓存副本而非 `playwright install`**：binding 期望的
   revision（1.65.2/1.64.0）与缓存（1234/1243/1254）不完全一致，但
   `executablePath` 指向 headless shell 实测可直接启动（R-01）。
3. **editable 构建的坑**：`uv sync --locked --dev` 在本机会栽在项目自身
   editable wheel 的 `EEXIST: mkdir`（与 playwright 无关）；用
   `uv pip install --python .venv/bin/python playwright` 绕开，
   lock 仍由 `uv lock` 正确同步（dev 组）。Docker 路径
   （`--no-dev --no-editable`）不受影响。
4. **拖拽预留退化路径**：dnd-kit PointerSensor 在无头 + 合成事件下可能
   不满足激活阈值——退化点选并注明，不假装拖拽通过（R-06）。
