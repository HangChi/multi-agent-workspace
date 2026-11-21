# Tasks: 部门与分派范围（020）

> 勾选纪律：只有实测通过的才 `[X]`。

## 规格

- [X] T001 spec.md（问题陈述 / 三档范围 / FR-001~009 / 错误码 / SC-001~005 / 范围外）
- [X] T002 research.md（R-01~R-08：物化路径 / 三档而非矩阵 / 默认不收紧 /
      下拉过滤 / 派给自己放行 / 删部门两条防线 / 先查再插 / 不做部门可见性）
- [X] T003 tasks.md（本文件）

## 后端

- [X] T101 迁移 `0020_departments`（departments 表 + users.department_id + 索引）
- [X] T102 模型 `Department`（物化路径、三档 scope、**刻意不声明 ORM 自引用关系**）
- [X] T103 `User.department_id` + 模型注册
- [X] T104 `org_service`：list_tree（含成员数）/ create / update / delete / set_user_department
- [X] T105 `create_department` 先查重名再 INSERT（否则 flush 撞 UNIQUE → 500）
- [X] T106 `allowed_target_department_ids`（all→None / self→{自身} / subtree→LIKE 子树）
- [X] T107 `can_assign_to`（派给自己永远放行；受限时未归属者不放行）
- [X] T108 `allowed_user_ids` → `user_service.list_options(db, actor_id)` 收窄
- [X] T109 API：`/departments` CRUD（读 member / 写 admin）+ 人员归属 + 成员映射
- [X] T110 转交门禁接线 `dispatch_service`（目标存在性之后、状态迁移之前）
- [X] T111 审计常量 `department_create` / `department_delete`
- [X] T112 集成测试 11 例（树/路径 / 重名 / 删除清归属 / 三档范围 / 派给自己 /
      选项过滤 / 非 admin 403 / admin CRUD / 端到端转交 403）
- [X] T113 后端门禁：ruff + format + mypy + pytest（全绿）

## 前端

- [X] T201 `api/departments.ts`（类型 + CRUD + 人员归属 + SCOPE_LABEL）
- [X] T202 `OrganizationPage`（部门树缩进 / 新建 / 改名 / 改范围 / 删除 / 人员归属）
- [X] T203 路由 `/settings/organization`（RequireAdmin）+ `SETTINGS_NAV` 入口
- [X] T204 页面测试 2 例（树渲染与范围文案、新建提交带上父与范围）
- [X] T205 前端门禁：eslint + tsc + vitest

## 验收与收尾

- [X] T301 `deploy/verify/s16_departments.py`（4 场景，需 admin 凭据）真实栈通过
- [X] T302 迁移 `0019 → 0020` 在真实库执行（容器 up 自动 migrate）
- [X] T303 README / docs 同步（页面表、已知限制、特性数量）
- [X] T304 提交

## 实测记录（2026-11-21）

| 门禁 | 结果 |
|---|---|
| 后端 pytest + 覆盖率 | **2074 passed**（基线 2063，新增 11），覆盖率 **94.41%** |
| ruff / format / mypy | 通过（373 文件格式检查） |
| 前端 vitest | **154 passed / 30 files**（新增 2） |
| eslint / tsc | 通过 |
| 真实栈 s16 | **4/4**（path/depth / 非 admin 403 / 受限派外部 403 + 派自己 202 / 无部门不限） |
| 迁移 | `0019 → 0020` 执行成功 |

### 两个被测试逼出来的修正

1. **自引用 ORM 关系写错方向会让整个 mapper 配置失败**（所有模型都起不来）。
   果断删掉关系声明——子树查询走 path，删父删子靠 DB 的 CASCADE，ORM 关系零收益。
2. **先 INSERT 再查重名 = 500**：`flush()` 就撞 UNIQUE。改成先查再插，
   DB 约束只作并发兜底。
