# Specification Quality Checklist: 定时调度（按规则自动建单并分配执行）

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-11-17
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
  - 规格正文不出现表名、端点、库名；仅在 Assumptions 中以实测证据的形式提及任务框架版本与现有创建契约字段，用于解释为何范围如此划定，不构成实现指令。
- [x] Focused on user value and business needs
  - 5 个用户故事均以一个具体用户动作开头，并各自给出「没有它用户会怎样」的优先级理由。
- [x] Written for non-technical stakeholders
  - 无框架/协议术语；周期表达式用「工作日每天 09:00」表述，「日期与星期取交集」用自然语言说明并解释与标准 cron 的差异。
- [x] All mandatory sections completed
  - User Scenarios & Testing / Requirements / Key Entities / Success Criteria / Assumptions 全部存在。

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
  - 0 处。三个范围歧义已在开工前用问答确认（完整闭环 / 跳过不补跑 / member 自管 + admin 可查看），其余以 Assumptions 明示默认值。
- [x] Requirements are testable and unambiguous
  - FR-014「不补跑」以「补偿需求数 = 0」判定（SC-004）；FR-012「同计划时刻只建一张单」以「需求数 = 1」判定（SC-005）；FR-020「不改写历史」以「被改写条数 = 0」判定（SC-009）。均为可计数的谓词，不是"应当正确"。
- [x] Success criteria are measurable
  - 9 条 SC 全部为数值或可计数断言：1 分钟内、90 秒内、100% 一致、0 条、1 条、1 张、100%、0 次、0 条。
- [x] Success criteria are technology-agnostic (no implementation details)
  - SC 只描述「偏差」「补偿需求数」「Run 结构一致」等结果，不指定如何度量（度量手法留给 quickstart）。
- [x] All acceptance scenarios are defined
  - 5 个故事共 22 条 Given/When/Then；每条 FR 至少被一条场景或 SC 锚定（见下方「FR 锚点核对」）。
- [x] Edge cases are identified
  - 10 条，覆盖表达式语义、触发密度、夏令时、时钟异常、并发重复触发、目标瞬间失效、模板引用失效、创建者降级、半成品需求、历史膨胀。
- [x] Scope is clearly bounded
  - 8 条「不含」，每条给出依据（阶段号 / 既有端点 / 架构边界 / 用户已确认取舍）。
- [x] Dependencies and assumptions identified
  - 明确 001–003 为前置；10 条 Assumptions，其中 4 条带实测依据。

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
  - 25 条 FR 全部有锚点，见「FR 锚点核对」。
- [x] User scenarios cover primary flows
  - 建规则 → 触发执行 → 错过处理 → 历史追溯 → 变更不污染历史，构成完整闭环。
- [x] Feature meets measurable outcomes defined in Success Criteria
  - SC-001…SC-009 各自对应至少一条 FR，无孤立 SC。
- [x] No implementation details leak into specification
  - 「不含分布式选主」写成结果约束（正确性靠幂等保证），不是"用 Redis 锁"；「计入成本」写成继承既有配额，不是"调 quota_service"。

## 与宪法的对齐核对

| 宪法条款 | 本规格的落点 | 结论 |
|---|---|---|
| I. SDD + TDD（规格先行） | 本次先出 spec，plan/tasks 随后；实现须每个 Phase 走红-绿 | 符合 |
| II. spec 不泄漏 HOW | 规格无表名/端点/库名，实现手法全部推后 | 符合 |
| II. 每条 FR 有可判定锚点 | 25/25 有锚点 | 符合 |
| II. 范围边界显式写「不含」 | 8 条并附依据 | 符合 |
| II. 未决事项 ≤3 且带标记 | 0 处未决，歧义已在开工前问答消解 | 符合 |
| III. 每 Phase 可独立上线 | US1+US2 即构成可用最小闭环（建规则 + 自动执行） | 符合 |
| III. 生产化要素不后补 | 审计（FR-022）、权限（FR-005/023）、历史保留（FR-021）在本阶段落地 | 符合 |
| IV. 关键路径须集成测试 | 触发链路属关键路径，SC-003/005 要求与手工分配结构一致 | 符合 |
| 安全：资源归属校验 | FR-005/006/023/025 显式要求，且 FR-006 禁止泄露资源存在性 | 符合 |
| 安全：成本熔断不可绕过 | FR-011、Assumptions「调度计入成本」 | 符合 |
| 安全：敏感操作审计 | FR-022 覆盖规则变更与每次触发 | 符合 |
| 技术栈：Celery + Beat | 与既有 Beat 并存，不引入第二套调度设施（plan 阶段细化） | 符合 |

## FR 锚点核对

| FR | 锚点 |
|---|---|
| FR-001 | US1 场景 1；US5 场景 3 |
| FR-002 | US1 场景 2、3；SC-001 |
| FR-003 | US1 场景 1、4；SC-002 |
| FR-004 | US1 场景 1；Edge Cases 第 1 条；Assumptions 第 1 条 |
| FR-005 | US1 场景 5；US4 场景 3、4 |
| FR-006 | US1 场景 5 |
| FR-007 | US1 场景 4；US5 场景 4；SC-008 |
| FR-008 | US2 场景 1、4；SC-003 |
| FR-009 | US2 场景 5；Edge Cases「需求创建成功但分配失败」 |
| FR-010 | US2 场景 3；US4 场景 2 |
| FR-011 | US2 场景 5；US3 场景 3；SC-008 |
| FR-012 | SC-005；Edge Cases「同一条规则被并发触发」 |
| FR-013 | US3 场景 2；SC-008 |
| FR-014 | US3 场景 1；SC-004 |
| FR-015 | US3 场景 1；SC-004 |
| FR-016 | US3 场景 5；US4 场景 1 |
| FR-017 | US3 场景 4 |
| FR-018 | SC-006 |
| FR-019 | US4 场景 1、2；SC-006 |
| FR-020 | US5 场景 1、2；SC-009 |
| FR-021 | Assumptions「触发历史保留」；Edge Cases 最后一条 |
| FR-022 | SC-007 |
| FR-023 | Edge Cases「创建者被降级」；SC-008 |
| FR-024 | SC-006 |
| FR-025 | Edge Cases「模板内容引用了已不存在的资源」 |

## Notes

- **实测推翻/修正了 3 处原设想**（写规格前已核对，避免写出无法满足的验收标准）：
  1. `docs/architecture.md` 的 `requests` 表列了 `deadline`，但真实表结构与 `RequestCreate` 契约都没有该字段 → 需求模板不含截止时间，并写进 Assumptions 与 Scope Boundaries。
  2. 周期表达式在「日期 + 星期」同时限定时的语义，与本项目任务框架的实际行为是**交集**（实测 `0 9 1 * 1` 只在"既 1 号又周一"触发），与标准 cron 的并集不同 → 规格明确取交集并要求在界面上告知用户，否则会出现"永远不按用户预期触发"的争议。
  3. 原设想「调度只建单、不分配」在问答中被否 → 改为完整闭环，SC-003 因此要求 Run 结构与手工分配 100% 一致（否则调度链路会静默退化）。
- 保留一条诚实约束：SC-006 的「响应劣化不超过既有归因判据」不是新的绝对阈值，而是复用 002/003 已建立的归因口径（含 150ms 绝对上限）。若该口径在 004 被质疑，SC-006 需随之重写，而不是另立一套数字。
