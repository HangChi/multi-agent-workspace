# Requirements Checklist: Agent 模板与一键克隆

**Branch**: `feat/005-agent-templates` | **Date**: 2026-11-18 | **Spec**: [spec.md](spec.md)

## Content Quality

- [x] 无实现细节泄漏（spec.md 不含表名/端点/框架名；这些只在 data-model.md 与 contracts/api.md）
- [x] 聚焦用户价值与 WHAT/WHY，不含技术方案
- [x] 面向非技术干系人可读（用户故事用「成员」视角叙述）
- [x] 所有必填章节完整：User Scenarios / Requirements / Success Criteria / Scope Boundaries / Key Entities / Assumptions
- [x] 不含 `[NEEDS CLARIFICATION]` 标记；未决项全部落入 Assumptions 并说明理由
- [x] 需求可测：每条 FR 有 Given/When/Then 场景或可计数判词
- [x] 范围边界显式声明「不含」6 项（共享市场、版本历史、授权复制、批量创建、导入导出、使用统计）
- [x] 成功标准可测量且与技术无关（步数/秒数、字段一致率、改写条数 0、覆盖率 100%）

## Requirement Completeness

- [x] 无模糊量词（「更快」「更好」均未出现；「≥3 个内置模板」「3 步 30 秒」为可判定值）
- [x] 每条 FR 有主语与 MUST 级措辞
- [x] 覆盖正常路径（US1/US2/US3）、边界（工具下线、重名、撞名、归档源）、失败路径（越权、内置只写、并发删除）
- [x] 安全类需求显式：归属校验（FR-004）、存在性不泄露（FR-004）、审计（FR-019）、不复制凭证（FR-020）
- [x] 删除/隔离语义显式：FR-002 双向隔离、FR-021 删除不相连
- [x] 资源归属模型与既有系统一致（member 管自己的，admin 无模板特权，viewer 403）
- [x] 与 004 的既有约定对齐：修改不改写历史（FR-002 ↔ 004 FR-020）、404 同措辞（FR-004 ↔ 004 FR-006）

## Feature Readiness

- [x] 用户故事按优先级排序且各自可独立测试（US1/US2/US3 为 P1，US4/US5 为 P2）
- [x] 每个 US 有 Why this priority + Independent Test
- [x] 验收场景覆盖 US 全部 Given/When/Then
- [x] Edge Cases 覆盖：工具下线、模型不可用、并发删除、重名、克隆撞名、归档源、用户删除、内置另存、空/超长提示词、前端绕过
- [x] Key Entities 与 data-model.md 一致（1 新实体 + 2 个可空来源列）
- [x] Success Criteria 与 FR 可回溯（见下方 SC→FR 映射）
- [x] Assumptions 记录全部未决项及其默认值与理由

## 与宪法的对齐核对

| 宪法条款 | 结论 | 说明 |
|---|---|---|
| I. SDD + TDD 双驱动 | 符合 | spec 评审后才进 plan；tasks.md 每个实现任务先写失败测试 |
| II. 规格先行 | 符合 | WHAT/WHY 完整，无 HOW 泄漏，边界显式 |
| III. 完整系统非 MVP | 符合 | 审计/归属/校验在本特性内落地（FR-019/004/009） |
| IV. 质量门禁 | 符合 | 每 Phase lint+format+mypy+pytest+覆盖率；前端五项门禁 |
| 技术栈约束 | 符合 | 零新增依赖、零新增进程 |
| 安全边界 | 符合 | 归属校验、404 同措辞、不复制凭证授权、日志脱敏由既有体系继承 |
| 开发工作流 | 符合 | `feat/005-agent-templates` 分支，每 Phase 一提交，偏离必改上游文档 |

## SC → FR 映射核对

| SC | 判定 | 锚定 FR |
|---|---|---|
| SC-001 3 步 30 秒 | 操作步数与耗时 | FR-005/006 |
| SC-002 8 字段 100% 一致 | 克隆产物字段比对 | FR-010 |
| SC-003 实例级数据全 0 | 计数断言 | FR-007/011 |
| SC-004 改写条数 0 | 计数断言 | FR-002/021 |
| SC-005 创建成功 100% + 提示覆盖 100% | 计数断言 | FR-008 |
| SC-006 越权 100% 同措辞 | 响应体断言 | FR-004 |
| SC-007 审计覆盖 100% | 记录比对 | FR-019 |
| SC-008 内置 ≥3 且只读 + 幂等 | 计数 + 403 断言 | FR-015/016 |
| SC-009 源组引用变化 0 | 计数断言 | FR-011/013 |

## FR → 用户故事锚点核对

| FR | 锚点 |
|---|---|
| FR-001/002/003 | US3 AS1-AS4 |
| FR-004 | US4 AS5、US2 AS5 |
| FR-005/006/007/008/009 | US1 AS1-AS5 |
| FR-010/011/012/013/014 | US2 AS1-AS5 + Edge Cases |
| FR-015/016/017/018 | US5 AS1-AS4 |
| FR-019/020/021/022 | 全部 US 的审计与隔离要求 |

## Notes

- 本特性的「模板」与 003 的「外部 MCP 工具市场」是两回事：前者是 Agent 配置的个人私有
  快照库，后者是外部工具服务目录。两者不共享任何表或端点。
- spec 明确排除「跨用户共享模板」，因此**不存在**「admin 可看全部模板」的需求；
  若将来要做团队模板库，需作为独立特性重新过 SDD 四步。
