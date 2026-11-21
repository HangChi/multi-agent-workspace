# Tasks: 项目成员角色（019）

> 勾选纪律：只有实测通过的才 `[X]`。

## 规格

- [X] T001 spec.md（问题陈述 / 权限矩阵 / FR-001~008 / 错误码 / SC-001~004）
- [X] T002 research.md（R-01~R-08：010 前提变了 / 只两值 / 403 / require_write /
      owner 特判 / 转交不改 / 前端显示 / 零回填）
- [X] T003 tasks.md（本文件）

## 后端

- [X] T101 迁移 `0019_project_member_role`（加列 + CHECK + server_default='editor'）
- [X] T102 模型 `ProjectMember.role` + `ROLE_EDITOR` / `ROLE_VIEWER` 常量
- [X] T103 `member_role()`：owner 优先级最高（owner 不在成员表，R-01）
- [X] T104 `can_write_in_project()`：owner ∪ editor
- [X] T105 `add_member(role=...)` + service 层非法角色 400（schema 的 Literal 之外的
      第二道）
- [X] T106 `list_members` 返回 5 元组（含 role），owner 行 role='owner'
- [X] T107 `get_request(require_write=True)`：成员快路里再判写权限（403）
- [X] T108 `update_request` 与 `assignment_service.assign` 接线 require_write
- [X] T109 schema：`ProjectMemberCreate.role`（Literal）、`ProjectMemberOut.role`
- [X] T110 端点：加成员传 role、列表映射 role
- [X] T111 集成测试 10 例（角色落库 / 默认 editor / viewer 能读 / viewer 改不了 /
      assign 403 / editor 能改 / owner 不被自己挡 / 陌生人仍 404 / 非法角色两处）
- [X] T112 后端门禁：ruff + format + mypy + pytest（全绿）

## 前端

- [X] T201 `ProjectRole` 类型 + `addProjectMember(projectId, userId, role)`
- [X] T202 成员区：角色徽章（只读/可编辑）、添加时选角色、只读成员视角说明
- [X] T203 组件测试 2 例（只读徽章、viewer 透传）
- [X] T204 前端门禁：eslint + tsc + vitest（全绿）

## 验收与收尾

- [X] T301 `deploy/verify/s15_project_roles.py`（4 场景）真实栈通过
- [X] T302 迁移 `0018 → 0019` 在真实库执行（容器启动自动 migrate）
- [X] T303 README 已知限制：A3 从「不设」改为已实现并写清边界
- [X] T304 规格实测回填 + 提交

## 实测记录（2026-11-21）

| 门禁 | 结果 |
|---|---|
| 后端 pytest + 覆盖率 | **2063 passed**（基线 2053，新增 10），覆盖率 **94.64%** |
| ruff / format / mypy | 通过 |
| 前端 vitest | **152 passed / 29 files**（新增 2） |
| eslint / tsc | 通过 |
| 真实栈 s15 | **4/4**（角色落库 / 只读能读 / 只读改不了 / editor 能改） |
| 迁移 | `0018 → 0019` 在真实库执行成功（容器 up 时自动 migrate） |

### 一处被测试逼出来的实现细节

`member_role()` 必须先判 `projects.owner_id` 再查成员行——owner 不在成员表里
（010 R-01），只查成员行会把创建者自己判成「不能写」。
`test_owner_is_not_blocked_by_role_check` 就是钉这个的。
