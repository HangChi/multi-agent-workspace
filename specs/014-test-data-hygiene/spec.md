# Feature Specification: 测试数据卫生（014）

**Feature Branch**: `feat/014-test-data-hygiene`

**Created**: 2026-11-18

**Status**: Draft

**Input**: 用户 2026-11-18 截图反馈：看板「拖到目标」面板被 303 个条目
淹没——其中 302 个是 s1–s12 验收脚本攒下的测试账号（每个脚本注册新用户
做种子、跑完从不清理）。用户要求「执行清理，并且这里的展示方式有点丑，
能不能优化」。

## User Scenarios & Testing *(mandatory)*

### US1 — 一键清理存量测试数据（优先级 P1）

作为维护者，我希望能用一条命令把验收脚本攒下的测试账号及其关联数据
清掉，只保留真实账号，且**删不坏东西**。

**Acceptance Scenarios**：
1. **Given** 库里有 303 用户（302 测试 + 1 真实），**When** 跑
   `cleanup_test_data.py --execute`，**Then** 剩余 1 用户，其数据
   （审计日志等）完整保留。
2. **Given** 清理脚本，**When** 不带 `--execute` 运行，**Then** 只打印
   将删什么，不动数据（dry-run 默认）。
3. **Given** 删除涉及 21+ 张表的外键网，**When** 任何一步失败，
   **Then** 整个事务回滚，不留半清状态。
4. **Given** 保护名单（默认 owner@example.com），**When** 清理，
   **Then** 名单内账号与其数据一行不碰。

### US2 — 目标面板在账号多了也好用（优先级 P1）

作为用户，我看板右侧的分配目标区应该**按类型分区、可折叠、可过滤**——
不再是一行滚不到头的芯片墙。

**Acceptance Scenarios**：
1. **Given** 有同事/Agent/组三类目标，**When** 看板渲染，**Then**
   三类各占一行，各带计数（同事 N / Agent N / 组 N）。
2. **Given** 同事超过 8 个，**When** 渲染，**Then** 只露 8 个 + 「+N 展开」。
3. **Given** 同事行，**When** 在过滤框输入邮箱片段，**Then** 列表实时
   缩小到匹配项。
4. **Given** 拖拽契约，**When** 拖需求到同事/Agent/组芯片，**Then**
   行为与旧版完全一致（useDroppable id 与 title 不变——s12 的 B4
   复跑必须仍 15/15）。

### Edge Cases

- **三类全空**：引导文案（旧版行为保留）。
- **过滤后 0 匹配**：同事行显示「还没有同事」提示，不报错。
- **保留用户引用受害者创建的邀请码**：删码时排除仍被引用的
  （`users.invite_code_id → invite_codes` 环的破法）。
- **自引用 FK**（`runs.parent_run_id`、`agents.source_agent_id`）：
  删行即删关系，不参与拓扑排序；实测无「保留行引用受害行」边案。

## Requirements *(mandatory)*

- **FR-001**： MUST 提供 `deploy/verify/cleanup_test_data.py`：dry-run
  默认、`--execute` 才删、邮箱前缀白名单 + 保护名单、单事务。
- **FR-002**：删除 MUST 走「污染传播（不动点）+ 拓扑排序（子先）」两
  阶段——只按用户列删会漏 `group_members` 这类没有用户列的中间表。
- **FR-003**：`users ↔ invite_codes` 的 FK 环 MUST 被显式处理（置 NULL
  破环 + 删码排除仍被引用者）。
- **FR-004**：`AssignTargets` MUST 重设为三行分区（同事/Agent/组），
  每行计数 + 折叠（阈值 8）+ 同事行过滤框。
- **FR-005**：拖拽契约 MUST 不变：`useDroppable` 的 `id` 与 `title`
  与旧版逐字一致。
- **FR-006**：MUST 有组件测试（`src/tests/assignTargets.test.tsx`）钉住
  行标签/计数/折叠/过滤/空态。
- **FR-007**：修完 MUST 经 s12 复跑验证仍 15/15。
- **FR-008**：清理执行前 MUST 有 `pg_dump` 全库备份；`.gitignore` MUST
  忽略 `deploy/backup/*.sql`。

### Key Entities

- `cleanup_test_data.py`：验收数据清理工具（新增）。
- `AssignTargets`：分配目标区组件（重写）。
- 不新增实体、不改后端、不加依赖。

## Success Criteria *(mandatory)*

- **SC-001**：实测清理 303 → 1 用户，关联数据 4210 行一并清除，
  保留账号数据完好（审计日志 3 条）。
- **SC-002**：s12 复跑 15/15（拖拽契约未破）。
- **SC-003**：前端五件套全绿，`assignTargets.test.tsx` 4 例通过
  （套件 139 → 143）。
- **SC-004**：截图确认新面板：三行分区 + 过滤框 + 计数，看板正常。

## Assumptions

- 存量清一次；增量由 UI 的过滤/折叠兜住（验收脚本仍会建测试账号，
  自动清理留作后续——不在本特性范围）。
- 清理范围按邮箱前缀白名单；新脚本若用新前缀，加进 `--pattern` 即可。
- 保护名单默认只有 admin；多真实账号时用 `--protect` 扩展。
