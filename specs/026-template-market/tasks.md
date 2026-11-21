# Tasks: 模板市场（026）

- [X] T101 `duplicate_template` service（可见性由 get_template 保证；副本恒 private）
- [X] T102 `_unique_copy_name`（「（副本）」→ 「（副本 2）」；与 import 的 409 相反）
- [X] T103 `POST /agent-templates/{id}/duplicate`
- [X] T104 `AgentTemplateOut.owner_email` + 列表一次 IN 查询补全
- [X] T105 测试 4 例
- [X] T106 前端：团队卡片「复制到我的」+「来自 xxx」
- [X] T107 前端断言：复制按钮只在别人的卡片上
- [X] T108 门禁：ruff / format / mypy / eslint / tsc / vitest

## 实测记录（2026-11-21）

| 门禁 | 结果 |
|---|---|
| 后端 | **2119 passed**（新增 4），覆盖率 **94.14%** |
| 前端 | 155 passed（复制按钮断言并入团队共享用例） |
| 真实栈 s21 | **3/3** |
