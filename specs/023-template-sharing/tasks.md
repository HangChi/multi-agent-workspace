# Tasks: 模板共享 / 版本历史 / 导入导出（023）

## 规格
- [X] T001 spec.md（问题 / 三块设计 / 权限矩阵 / FR-001~010 / SC / 范围外）
- [X] T002 research.md（R-01~R-08）
- [X] T003 tasks.md

## 后端
- [X] T101 迁移 `0023_template_versions`（visibility + 版本表 + 两个索引）
- [X] T102 `AgentTemplateVersion` 模型 + `KEEP_VERSIONS`
- [X] T103 `snapshot_of` + `_record_version`（改前快照 + 裁剪到 20）
- [X] T104 `update_template` 改前记录（含 owner 判定——共享后必须显式判）
- [X] T105 `list_versions` / `restore_version`（回滚也记历史、不还原 name）
- [X] T106 `export_payload` / `import_template`（schema_version + 创建 schema 复校验）
- [X] T107 `get_template` 放开 team 可见；写操作各自判 owner
- [X] T108 `list_templates` 支持 `scope=team`，`all` 含 team
- [X] T109 端点：versions / restore / export / import（import 在 `{id}` 之前注册）
- [X] T110 测试 6 例（改前快照 / 回滚不改名 / 导出导入往返 / 版本号不识别 /
      team 可见不可改 / 内置派生 team）
- [X] T111 门禁：ruff + format + mypy + pytest

## 前端
- [X] T201 `api/agentTemplates.ts`：visibility + versions/restore/export/import
- [X] T202 「团队共享」分组（可用不可改，不渲染编辑/删除）
- [X] T203 「历史 / 共享」弹层：共享开关 + 版本列表 + 回滚 + 导出 JSON
- [X] T204 顶部「导入模板」（file input → POST）
- [X] T205 门禁：eslint + tsc + vitest

## 验收
- [X] T301 `s19_template_sharing.py` 3/3
- [X] T302 迁移 `0022 → 0023` 真实库执行
- [X] T303 文档与提交

## 实测记录（2026-11-21）

| 门禁 | 结果 |
|---|---|
| 后端 | **2098 passed**（基线 2091，新增 6 + 1 skip），覆盖率 **94.32%** |
| 前端 | **155 passed / 31 files**（新增 1） |
| 真实栈 s19 | **3/3**（改前快照+回滚 / 导出导入 / team 可见不可改） |
| 迁移 | `0022 → 0023` 成功 |

## 关键发现

**共享让「可见」与「可改」分家**：`get_template` 放开 team 可见后，若写操作继续复用
它，队友就能改你的模板。集成测试直接抓到（PATCH 返回 200）。已在 `update_template`
与 `restore_version` 里显式判 owner，并在代码注释里留了这条教训。
