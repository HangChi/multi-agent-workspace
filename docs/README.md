# 文档索引

项目总览、快速开始、API 概览、配置项、测试与部署都在根目录
[README.md](../README.md)。本目录只放**深入设计与专项记录**。

## 设计文档

| 文档 | 内容 |
|---|---|
| [architecture.md](architecture.md) | 完整设计文档：系统定位、领域模型、技术选型理由、执行引擎（状态机 / ReAct / 组策略）、安全设计、数据表与索引、可观测性、分阶段实施计划 |

## 专项记录

| 文档 | 内容 |
|---|---|
| [llm-pricing.md](llm-pricing.md) | LLM 单价表口径：每个数字都要能追到厂商官方刊例价，错一个数只会让配额与熔断**静默**偏松或偏紧 |
| [load-test-2026-11-17.md](load-test-2026-11-17.md) | 100/200 并发压测记录（2 vCPU 开发机，818 req/s / 0 错误） |
| [code-audit-2026-11-17.md](code-audit-2026-11-17.md) | 2026-11-17 全量代码审查报告：范围、方法、修复进度表、遗留项 |
| [audit-followup-2026-11-20.md](audit-followup-2026-11-20.md) | **审计跟进**：11-17 遗留项与本轮处置逐条对照（已闭环 / 未闭环及原因） |

## 功能规格（Spec Kit，`../specs/`）

二十一个特性都按 SDD + TDD 走完，每个目录结构一致：
`spec.md`（WHAT/WHY）→ `plan.md` / `research.md` / `data-model.md` /
`contracts/api.md` → `checklists/requirements.md` → `tasks.md`（逐条勾选 + 实测记录）
→ `quickstart.md`（真实栈验收）。

| 特性 | 内容 | 状态 |
|---|---|---|
| [001-core-workflow](../specs/001-core-workflow/spec.md) | 拖拽分配、四种编排策略、ReAct 执行引擎、实时反馈、成本熔断 | ✅ 84/84 |
| [002-sandboxed-tools](../specs/002-sandboxed-tools/spec.md) | 代码沙箱（Docker 一次性容器）、出网治理、工具目录与授权 | ✅ 76/76 |
| [003-mcp-tool-market](../specs/003-mcp-tool-market/spec.md) | 外部 MCP 工具：注册、审批、按 Agent 授权、调用留证 | ✅ 65/65 |
| [004-scheduled-requests](../specs/004-scheduled-requests/spec.md) | 定时调度：cron 规则自动建单分配，错过不补跑 | ✅ 65/65 |
| [005-agent-templates](../specs/005-agent-templates/spec.md) | Agent 模板、一键克隆、另存为模板、内置模板 | ✅ 39/39 |
| [006-run-retry](../specs/006-run-retry/spec.md) | 运行自动重试：瞬时失败重试、不重复执行、成本跨次累计 | ✅ 39/39 |
| [007-request-dispatch](../specs/007-request-dispatch/spec.md) | 需求分派给员工、收件箱、站内通知、经手链 | ✅ 41/41（s8 真实栈 7/7） |
| [008-project-entity](../specs/008-project-entity/spec.md) | 项目容器：需求归类、看板项目筛选、项目 CRUD/归档/删除 | ✅ 30/30（s9 真实栈 5/5） |
| [009-request-dependency](../specs/009-request-dependency/spec.md) | 需求依赖：有向边、硬门禁（409 逐条）、解锁通知、成环拒绝 | ✅ 30/30（s10 真实栈 4/4） |
| [010-project-sharing](../specs/010-project-sharing/spec.md) | 项目共享：成员管理、共享可见性、协作谓词（owner+成员两层） | ✅ 26/26（s11 真实栈 4/4） |
| [011-browser-walkthrough](../specs/011-browser-walkthrough/spec.md) | 浏览器走查基建：s12 脚本自动化三份前端清单，零应用改动 | ✅ 21/21（s12 终态 15/15） |
| [012-error-message-passthrough](../specs/012-error-message-passthrough/spec.md) | ErrorBanner 字符串直通：修 011 抓出的「后端错误消息全被兜底文案吃掉」 | ✅ 10/10（s12 B4 复跑转 ✅） |
| [013-s12-findings-fix](../specs/013-s12-findings-fix/spec.md) | s12 挂账三项：viewer 入口/共享项目可见/依赖弹层刷新 | ✅ 11/11（s12 15/15） |
| [014-test-data-hygiene](../specs/014-test-data-hygiene/spec.md) | 测试数据卫生：清理验收账号 + 分配目标面板三行分区重设计 | ✅ 12/12（303→1 用户实测） |
| [015-ui-refresh](../specs/015-ui-refresh/spec.md) | 前端全模块 UI 整改：焦点/表单/缓存键基建收敛 + 三轮定点视觉迭代 | ✅ 23/24（17 提交 / 69 文件；143 例 + s12 15/15）。T024 视觉回归基线按决议暂缓，原因见 tasks.md |
| [016-error-code-unification](../specs/016-error-code-unification/spec.md) | 错误码语义统一：非相关人 403 → 404（防存在性枚举），参与者缺权限仍 403 | ✅ 14/14（2004 例 + 覆盖率 95.00%） |
| [017-project-ownership-transfer](../specs/017-project-ownership-transfer/spec.md) | 所有权移交：交给现有成员、`keep_as_member` 决定去留、六步校验、同名冲突回滚 | ✅（后端 2024 例 / 前端 148 例 / e2e 17 例 + 真实栈 10/10） |
| [018-external-notification-channels](../specs/018-external-notification-channels/spec.md) | 外部通知：Webhook 通道、事件订阅、HMAC 签名、退避重试、投递记录、SSRF 守卫 | ✅（后端 2053 例 / 前端 150 例 / e2e 19 例 + 真实栈 5/5） |
| [019-project-member-roles](../specs/019-project-member-roles/spec.md) | 成员角色：editor / viewer 两档，只读成员能看不能改（改需求/分配 403） | ✅（后端 2063 例 / 前端 152 例 + 真实栈 4/4） |
| [020-departments](../specs/020-departments/spec.md) | 部门与分派范围：部门树（物化路径）+ 三档 scope + 人员归属，默认不收紧 | ✅（后端 2074 例 / 前端 154 例 + 真实栈 4/4） |
| [021-dependency-graph](../specs/021-dependency-graph/spec.md) | 依赖图谱：下游子图 + 拓扑序 + 关键路径 + 自动启动（默认关，三道幂等校验） | ✅（后端 2084 例 / e2e 21 例 + 真实栈 4/4） |

> `research.md` 是每个特性里最值得先读的文件：它记录每个关键决策「为什么更简单的做法不行」，
> 例如 005 的「内置模板唯一性必须用部分唯一索引」（PostgreSQL 唯一约束遇 NULL 不生效）。

## 部署与运维（`../deploy/`）

| 文档 | 内容 |
|---|---|
| [../deploy/README.md](../deploy/README.md) | 服务清单、启动、LLM 端点接入、生产注意、日志排查、备份、升级、验证脚本 |
| [../deploy/DEPLOY.md](../deploy/DEPLOY.md) | 部署命令速查（`deploy.sh` 的顺序与理由） |

## 测试与验收（`../e2e/` 与 `../deploy/verify/`）

| 位置 | 内容 |
|---|---|
| [../e2e/README.md](../e2e/README.md) | **常驻浏览器 e2e**：pytest + Playwright，登录/导航/项目/需求/依赖五组共 14 例；环境变量、失败留证、加用例的三条约定（数据经 API 造 / 详情等两段式 / select 用 value） |
| [../deploy/verify/](../deploy/verify/) | 真实栈验收与一次性走查：s1–s12 场景脚本、压测、验收账号清理、告警规则门禁 |

## 其他位置

| 位置 | 内容 |
|---|---|
| [../.specify/memory/constitution.md](../.specify/memory/constitution.md) | 项目宪法：SDD + TDD 双驱动、质量门禁、安全边界、分支与提交规范 |
| [../.specify/templates/](../.specify/templates/) | Spec Kit 的 spec/plan/tasks 模板 |

---

> **关于 architecture.md 的版本**：它是 v0.1 初始设计，写作时沙箱、MCP、调度、监控都还是
> 「规划中」。这些能力已全部落地（002/003/004 + 可观测性），文中相应章节仍保留原始设计
> 取舍，但**实际已实现范围以根 README 的「已知限制」和各特性的 spec 为准**；
> 第 12 节的分阶段计划末尾附了实现进度。
