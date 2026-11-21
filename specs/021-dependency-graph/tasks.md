# Tasks: 依赖图谱与自动启动（021）

## 规格
- [X] T001 spec.md（问题 / 图谱设计 / 权限 / FR-001~009 / SC / 范围外）
- [X] T002 research.md（R-01~R-08）
- [X] T003 tasks.md

## 后端
- [X] T101 迁移 `0021_request_auto_start`（auto_start + auto_agent_id FK SET NULL）
- [X] T102 `dependency_graph_service`：下游子图 BFS + Kahn 拓扑 + 最长链 + unmet_count
- [X] T103 节点上限与 `truncated` 标记（不静默截断）
- [X] T104 `GET /requests/{id}/dependency-graph`（读权限与详情一致）
- [X] T105 `RequestUpdate` 支持 auto_start / auto_agent_id（权限=创建者或项目 owner）
- [X] T106 `auto_agent_id` 校验（必须属于该需求创建者，否则 404）
- [X] T107 任务 `auto_start_request`（三道幂等校验 + assign + commit 后入队）
- [X] T108 settle 钩子 enqueue（TESTING 短路；入队失败不影响 settle）
- [X] T109 测试 10 例（4 纯算法 + 6 端点/权限）
- [X] T110 门禁：ruff + format + mypy + pytest

## 前端
- [X] T201 `fetchDependencyGraph` + 图谱类型 + `updateRequest` 扩展字段
- [X] T202 `DependencyChain`（拓扑序列表 + 关键路径徽章 + 自动启动开关 + Agent 选择）
- [X] T203 半配置保护（勾自动启动未选 Agent → 保存禁用）
- [X] T204 门禁：eslint + tsc + vitest

## 验收
- [X] T301 `s17_dependency_graph.py` 4/4（图谱 / 配置 / 权限 / 404）
- [X] T302 e2e 2 例（下游链可见 / 半配置禁用），全量 21 passed
- [X] T303 迁移 `0020 → 0021` 真实库执行；worker 注册 `auto_start_request`
- [X] T304 文档与提交

## 实测记录（2026-11-21）

| 门禁 | 结果 |
|---|---|
| 后端 | **2084 passed**（基线 2074，新增 10），覆盖率 **94.17%** |
| 前端 | **154 passed**（无新增单测；图谱由 e2e 覆盖） |
| e2e | **21 passed**（19 + 2） |
| 真实栈 s17 | **4/4** |
| 迁移 | `0020 → 0021` 成功 |

**未验**：自动启动的真实执行（需前置真的跑完）——已在 spec 与 quickstart 标注。
