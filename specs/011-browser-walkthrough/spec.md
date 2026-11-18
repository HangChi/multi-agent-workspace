# Feature Specification: 浏览器走查验收（011）

**Feature Branch**: `feat/011-browser-walkthrough`

**Created**: 2026-11-18

**Status**: Draft

**Input**: 用户 2026-11-18 对「挂账的浏览器人工走查」拍板「要」——把 008/009/010
三个特性 quickstart 里 ⏳ 的前端走查清单自动化。范围经二次确认：
**三份清单 15 项全覆盖**；脚本入 `deploy/verify/` 家族（s12，Python）；
种子数据走 API。

## User Scenarios & Testing *(mandatory)*

### US1 — 一条命令跑完三份清单（优先级 P1）

作为维护者，我希望一条命令（`backend/.venv/bin/python deploy/verify/
s12_browser_walkthrough.py --base ... --invite ...`）就把 008/009/010 的
前端走查清单全部跑完，输出逐项 ✅/❌——把三个 ⏳ 变成有实测记录的 ✅。

**Why this priority**：这是特性的全部意义——15 项清单此前靠人肉浏览器点，
没有命令就没有「未实测不勾选」纪律的兑现渠道。

**Independent Test**：起栈 + 跑 s12 → A/B/C 三组各出 ✅/❌ 行 → 退出码
反映总结果。不需要任何其他特性配合。

**Acceptance Scenarios**：

1. **Given** 栈就绪，**When** 跑 s12，**Then** 输出 A(008)/B(009)/C(010)
   三组共 15 项逐项 ✅/❌，末行汇总「合计 15 项：N 通过 / M 失败」。
2. **Given** 任一项断言失败，**When** 跑完，**Then** 退出码非零、失败项
   落截图 + 页面关键 DOM 到产物目录（默认 `deploy/verify/.artifacts/`）。
3. **Given** 前端 dev server 未启动，**When** 跑 s12，**Then** 脚本自行
   拉起（`npm run dev -- --port <port>`）并在结束时回收（finally），
   端口占用时给出明确错误而不是挂死。
4. **Given** 本机无可用的 Chromium，**When** 跑 s12，**Then** 明确失败并
   提示 `S12_CHROMIUM_PATH` 环境变量用法（不静默跳过——静默跳过的验收
   等于没验收）。

### US2 — 008 清单自动化（优先级 P1）

作为维护者，我希望项目容器特性的 5 项前端行为（管理页 CRUD、建需求选择器、
看板项目下拉与 URL 深链、卡片徽章、viewer 门）被逐项断言。

**Why this priority**：008 的 T029 是第一个挂账项；其清单覆盖项目管理页
（010 的成员区也挂在这页，先验证基础 UI 在后）。

**Independent Test**：只跑 s12 的 A 组（API 建 1 项目 + 2 需求 + 1 viewer），
5 项全 ✅。

**Acceptance Scenarios**：

1. `/projects` 管理页：列表显示名称/状态/需求数；新建项目出现在列表；
   行内改名后卡片标题更新；归档切换后徽章变「已归档」；删除有确认弹窗且
   文案说明需求保留。
2. 建需求表单：项目选择器默认「不归类」；下拉仅列本人 active 项目
   （已归档与他人项目不可见）。
3. 看板工具条：项目下拉含「全部项目/各项目（带计数）/未归属」；选中后
   URL 出现 `?project=<id>`；刷新后筛选保持。
4. 需求卡片：有归属显示项目徽章；无归属不渲染。
5. viewer 登录：看板无项目下拉；`/projects` 无入口（导航不可见）。

### US3 — 009 清单自动化（优先级 P2）

作为维护者，我希望依赖特性的 5 项前端行为（详情依赖区块、blocked 提示、
卡片前置徽章、被挡分配的错误横幅、铃铛通知跳转）被逐项断言。

**Why this priority**：009 的 T025；依赖区块是详情弹层里最重的交互，
且错误横幅依赖 409 的真实返回（前后端联结点）。

**Independent Test**：只跑 s12 的 B 组（API 建 A/B 两条需求 + B 依赖 A），
5 项全 ✅。

**Acceptance Scenarios**：

1. 详情弹层「前置需求」区块：显示边（标题+状态徽章）；添加器能连一条
   新边；移除按钮能删边。
2. blocked 提示：前置未完成时区块顶部显示「有 N 个前置未完成，暂不能分配执行」。
3. 卡片徽章：未完成前置显示「N 个前置未完成」+ hover 标题；前置终态后
   徽章消失。
4. 错误横幅：拖（或点选）被挡住的需求给 Agent → 错误横幅逐条列出阻塞项
   （含前置标题）。
5. 铃铛：收到「前置就绪」通知；点开跳到后续需求详情。

### US4 — 010 清单自动化（优先级 P2）

作为维护者，我希望共享特性的 5 项前端行为（成员区管理、成员视角只读、
成员看板与表单可见共享项目、铃铛成员通知、viewer 门）被逐项断言。

**Why this priority**：010 的 T025；成员区涉及双角色切换（owner/成员），
是前端权限可见性的收尾验证。

**Independent Test**：只跑 s12 的 C 组（API 建项目 + 2 成员 + 1 viewer），
5 项全 ✅。

**Acceptance Scenarios**：

1. owner 视角：项目页成员区显示创建者徽章 + 成员列表 + 添加器 + 移除按钮；
   添加一个成员后列表更新。
2. 成员登录：项目卡显示「共享的」徽章；成员区只读（无添加器/移除按钮）。
3. 成员看板：项目下拉含共享项目；建需求表单的项目选择器含共享项目。
4. 铃铛：成员收到「你已被加入项目 X」通知。
5. viewer 登录：无项目页入口、无成员操作入口。

### Edge Cases

- **dev server 端口占用**：默认 5199（非常用端口），`--frontend-port` 可改；
  占用时明确报错退出，不静默换端口（换端口会让用户对不上日志）。
- **浏览器缺失**：按 `S12_CHROMIUM_PATH` → 扫描 `PLAYWRIGHT_BROWSERS_PATH`
  （默认 `~/Library/Caches/ms-playwright`，取最高版本 headless shell）→
  Playwright 默认顺序解析；都失败则明确退出并提示（US1 场景 4）。
- **登录态**：全部经 UI 登录页真实登录（登录流程本身也是被验收的一部分）；
  不用 localStorage 注入（注入等于跳过登录验证）。
- **拖拽（009 场景 4）**：dnd-kit 用 PointerSensor——Playwright 的
  `mouse.move/down/up` 序列可触发；若拖拽传感器在该环境不响应，
  退化为点选分配路径（AssignTargets）并**在输出里注明退化**——
  不假装拖拽通过。
- **多角色**：owner/mate/viewer 三个浏览器上下文（context 间 cookie 隔离），
  不反复登出登入（慢且抖）。
- **后端未起**：与 s1–s11 同样依赖 `--base` 可达；不可达时首项即失败并
  给出连接错误（不重试 30 秒）。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**：系统 MUST 提供 `deploy/verify/s12_browser_walkthrough.py`
  （Python + Playwright 驱动 Chromium），CLI 与 s1–s11 同构
  （`--base` / `--invite` / `--viewer-invite` / `--frontend-port` /
  `--frontend-url`），逐项 ✅/❌ 输出 + 汇总行 + 退出码。
- **FR-002**：浏览器解析 MUST 按序：`S12_CHROMIUM_PATH` 环境变量 →
  扫描 `PLAYWRIGHT_BROWSERS_PATH`（默认 `~/Library/Caches/ms-playwright`）
  取最高版本 `chromium_headless_shell-*` 的 `chrome-headless-shell` 二进制 →
  Playwright 默认（本机 Chrome for Testing）。全失败 → 明确退出并提示。
- **FR-003**：种子数据 MUST 走 API（httpx）：注册三个用户
  （owner/mate/viewer）、建项目、建需求、连依赖边、加成员、建 Agent；
  **不直连数据库**（绕应用层的种子会漏掉校验与审计）。
- **FR-004**：前端 dev server MUST 由脚本自管理：未提供 `--frontend-url`
  时自行 `npm run dev -- --port <port> --strictPort`，轮询就绪后继续，
  finally 回收子进程；`--frontend-url` 可指向已运行的实例（CI 场景）。
- **FR-005**：15 项清单断言 MUST 与三份 quickstart 的走查条目一一对应
  （§US2/3/4），输出行带清单编号（如 `A3`）便于回填 quickstart。
- **FR-006**：失败项 MUST 落产物：截图 PNG + 该页可见文本摘要到
  `deploy/verify/.artifacts/s12-<timestamp>/`，输出行注明路径；
  产物目录 MUST 进 `.gitignore`（截图是证据不是交付物）。
- **FR-007**：拖拽断言 MUST 用真实指针事件序列（mouse down/move/up）；
  传感器不响应时允许退化为点选分配，但 MUST 在输出行注明「（退化：点选）」
  ——不假装拖拽通过。
- **FR-008**：跑完后 MUST 回填三份 quickstart：把对应 ⏳ 条目改为 ✅ 并附
  日期与 s12 输出摘要（「未实测不勾选」纪律的兑现端）；未通过的项保持
  ⏳ 并在条目下注明失败原因。
- **FR-009**：本特性 MUST NOT 改任何应用代码（前端/后端零改动——
  纯新增验收脚本与文档）；Playwright MUST 只进后端 dev extras
  （`uv.lock` 的 dev 组）——Dockerfile `uv sync --locked --no-dev`
  不装它，运行时不变量不受影响；安装需代理（沙箱直连 502，
  见 research R-02）。
- **FR-010**：脚本 MUST 在 README 的验收脚本表里登记（s12），
  quickstart §4 门禁段补一条 s12 命令。

### Key Entities

- **走查项（Walkthrough Item）**：三份 quickstart 清单里的一条，编号
  A1–A5 / B1–B5 / C1–C5，一条对应脚本里一个断言组 + 回填一行。
- **产物（Artifacts）**：失败项的截图 + DOM 摘要，按次运行分目录
  （时间戳），只作排查证据。
- 不新增：e2e 测试框架依赖（Playwright Test）、页面对象模型库、
  数据库种子工具（API 种子已够）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**：s12 一条命令跑完 15 项，输出逐项 ✅/❌ + 汇总；全过时
  退出码 0，有失败时非零（实测：本轮 15/15）。
- **SC-002**：三份 quickstart 的 ⏳ 条目全部变为 ✅（附日期+输出摘要）或
  保持 ⏳ 附失败原因——**零个「跑了但没记录」**。
- **SC-003**：全流程（起 dev server + 登录 + 15 项断言）墙钟时间
  < 180s（单次真实栈实测；超了就把等待策略从固定 sleep 改为条件轮询）。
- **SC-004**：脚本可重复运行——第二次跑同样 15/15（种子数据用随机
  后缀，不依赖空库；断言不依赖上一次的残留状态）。
- **SC-005**：失败可诊断——人为破坏一项（如临时改选择器文案）时，
  该项 ❌ + 截图产物 + 退出码非零（实测一次，证明验收不是永远 ✅ 的
  摆设）。

## Assumptions

- 运行环境有 Node ≥18（跑 dev server）与至少一个可用 Chromium（本机缓存
  或 `S12_CHROMIUM_PATH` 指定）；都没有时脚本明确失败而非跳过。
- 后端 `--base`（默认 localhost:8080）可达；前端 dev server 由脚本按需
  拉起，用户也可用 `--frontend-url` 指向已有实例。
- viewer 角色经 `--viewer-invite`（可选）注册；不提供则 viewer 相关项
  （A5/C5）标记为「跳过（未提供 viewer 邀请码）」并**不勾选**对应
  quickstart 条目（同 s9 的诚实跳过纪律）。
- 拖拽在无头 Chromium 里对 dnd-kit PointerSensor 通常可用；不可用时
  按 FR-007 退化并注明。
- 本特性不改应用代码——发现的前端 bug 另开特性修（本特性只负责「发现」
  与「记录」，修不归它）。
