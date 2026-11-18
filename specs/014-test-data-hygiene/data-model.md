# Data Model: 014 测试数据卫生

零 schema 变更。两个交付物：

## 1. `deploy/verify/cleanup_test_data.py`

- 输入：邮箱前缀白名单（w-/dep-/disp-/verify-/proj-/share-/strategy-）+
  保护名单（owner@example.com）。
- 算法：污染传播（不动点）→ 拓扑排序（子先）→ 单事务删除。
- 实测（2026-11-18）：303 → 1 用户；13 张表 4210 行；保留账号的
  3 条审计日志完好。

## 2. `frontend/src/components/board/AssignTargets.tsx`（重写）

| 行 | 内容 | 交互 |
|---|---|---|
| 同事 (N) | 员工芯片（转交责任） | 过滤框（邮箱子串）+ 超 8 折叠 |
| Agent (N) | 执行者芯片 | 超 8 折叠 |
| 组 (N) | 组芯片（路由策略） | 超 8 折叠 |

契约不变：`useDroppable({ id: '${kind}:${id}' })`、title
「拖到这里分派给/分配给「name」 · meta」。

## 明确不做

- ❌ 验收脚本跑完自动清理（增量由 UI 兜住；自动清理留作后续）。
- ❌ 后端零改动、零新依赖。
- ❌ 改拖拽语义/ title 文案。
