# Requirements Checklist: 008 项目实体与需求归属

逐条带核对说明。**Notes** 记实测/决策推翻初始假设的地方。

## Spec 质量核对

| 条目 | 状态 | 说明 |
|---|---|---|
| 每个 US 独立可测 | ✅ | US1（建+归类+筛）、US2（管理）、US3（看板筛选）各自能单独演示 |
| FR 有 Given/When/Then 锚点 | ✅ | FR-001~012 分别对应 US1/US2/US3 的编号场景 |
| 边界单独成故事或成节 | ✅ | Edge Cases 节覆盖归档/删除/并发/越权改归属；US2 专管管理动作 |
| 「不含」每项有依据 | ✅ | FR-011（用户路线图后置 P2）+ data-model §5 四条 |
| 未决事项 ≤3 且无 NEEDS CLARIFICATION | ✅ | 0 个未决项；所有选择进 Assumptions 并带依据 |
| SC 可判定 | ✅ | SC-001 计时（s9）、SC-002 计数断言、SC-004 请求数、SC-006 逐字段回归 |
| 量化依据来自实测 | ✅ | Assumptions 第 1 条引用 2026-11-18 本地库实测（5 用户 / 2–6 条） |

## 与宪法（v1.1.0）的对齐核对

- [x] I. SDD+TDD：tasks.md 中每个实现任务前有对应测试任务（红→绿）。
- [x] I. 未实测不勾选：Phase 6 才允许勾验收类任务；浏览器项标 ⏳ 并写动作。
- [x] II. 范围边界：依赖边/DAG/成员表/统计物化四项显式排除（spec FR-011 + data-model §5）。
- [x] III. 完整系统：CRUD + 归档 + 删除 + 审计 + 权限分流 + 深链，无「半截」环节。
- [x] IV. 质量门禁：每 Phase ruff/mypy/pytest；P4 末前端五件套；P5 s9 + s8 回归。
- [x] 技术栈零新增依赖；不碰执行引擎/状态机/配额/Run 归属（FR-011）。
- [x] 归属纪律延续：项目 404（非创建者）、viewer 403（项目端点）、
      需求 `project_id` 变更 403（非创建者，R-05）。
- [x] 审计：project_create/update/delete 三动作 + 需求归属变更并入 request_update detail。

## 与既有契约的对齐核对

- [x] 007 访问权谓词（owner-or-assignee）在**不含** project_id 的编辑上原样保留。
- [x] 004 source_schedule_name 模式（可空展示快照）复用到 project_name。
- [x] 005 UNIQUE(owner_id, name) + 409 模式复用到项目重名。
- [x] 006 锁定契约：requests 既有端点错误码不变（404/403/409/422 分工不变）。
- [x] 列表响应只增字段（project_id/project_name 可空默认 null），SC-006 据此判零回归。

## Notes（实测/决策记录）

1. **初始想过「经手人也能改归属」**（与 007 谓词一致最省事）——写 US 场景时发现
   它让创建者的项目视图静默丢活且无任何痕迹，改为仅创建者可改（R-05）。
   这是本规格唯一被「写场景」推翻的设计，值得记住：**权限问题要在具体场景里
   走一遍，只在代码里看谓词看不出来**。
2. **`?project=none` 而非 `?project_id=null`**：R-06。query 里 "null" 字符串有
   四种序列化形态，哨兵值没有歧义。
3. **删除项目第二感是 RESTRICT**（防误删）——实测推演 20 条需求就要先改 20 条
   归属，删除动作不可用；改 SET NULL + 审计记 request_count 补救可观测性（R-02）。
4. 迁移号 0012 已按序列确认（0011_request_dispatch 之后）。
