# Feature Specification: 模板共享 / 版本历史 / 导入导出（023）

**Status**: Implemented（2026-11-21）

**Input**: 用户 2026-11-21「继续 A7」。A7 原描述为「模板共享 / 模板市场 /
版本历史 / 导入导出」，005 当时全部划在范围外（「模板是纯个人私有快照」）。

## 问题陈述

005 的模板只能自己用、改了不能回去、也不能带走。四个具体后果：

1. **好配置传播不了**：某人调好的提示词与工具集，同事只能截图或手抄；
2. **改坏回不去**：模板没有历史，一次误改就是永久损失（Agent 还有克隆，模板没有）；
3. **换环境要重敲**：从测试环境到生产、从公司到另一套部署，没有可移植格式；
4. **共享与可改分不清**：一旦允许别人用，就必须回答「他能不能改我的」。

## 设计

### 1. 版本历史（新表 `agent_template_versions`）

- 每次**成功修改**前，把当前状态存成新版本（`version` 在模板内自增）。
- **存完整快照，不存 diff**：模板只有 9 个配置字段 + 名字，体积可忽略；
  而 diff 要按序重放才能还原，回滚时错一步就得到半新半旧的配置。
- 回滚 = 把快照写回，且**本身也记一条历史**（否则「回滚错了再回滚」没有目标）。
- 保留最近 **20** 个：模板的历史值钱的是「昨天改坏了」。
- 回滚**不还原名字**：名字可能已被别的模板占用，还原名字会让回滚 409 变死路。

### 2. 导入导出

- `GET /agent-templates/{id}/export` → `{schema_version: 1, name, description, 9 字段}`。
- `POST /agent-templates/import` → 新建一份**自己的**模板；重名 **409**（不自动加后缀）。
- 导入用 `AgentTemplateCreate` **复校验**：字段范围、工具白名单都在这里，
  导入不构成绕过 005 校验的旁路（同 FR-022 纪律）。

### 3. 共享（visibility）

- `private`（默认）/ `team`。team：所有成员可见、可「从模板创建」，**只有所有者能改**。
- 内置模板（`owner_id IS NULL`）派生为 team（不落库，避免两个真相源）。
- `GET /agent-templates?scope=team` 列出别人的 team 模板；
  `scope=all` 扩大为「内置 + 自己的 + team 的」（只扩大不缩小，同 007 纪律）。
- **不做公开市场**：内容平台需要审核、举报、评分、版权，而内部工具的诉求只是
  「同事能用我调好的配置」。

## 权限

| 动作 | owner | 其他成员 | 内置 |
|---|---|---|---|
| 读 / 从模板创建 | ✅ | ✅（team）/ ❌（private → 404） | ✅ |
| 编辑 / 删除 / 回滚 | ✅ | ❌ 403 | ❌ 403 |
| 改 visibility | ✅ | ❌ 403 | — |
| 看历史 | ✅ | ✅（team） | — |

## 需求

- **FR-001** `agent_templates.visibility`（private/team，CHECK）+ 索引；迁移 0023 默认 private。
- **FR-002** `agent_template_versions`（template_id FK CASCADE / version / snapshot JSON /
  created_by / created_at / UNIQUE(template_id, version)）。
- **FR-003** 修改前记快照；保留最近 20 个。
- **FR-004** `GET /{id}/versions`、`POST /{id}/versions/{v}/restore`（回滚也记历史）。
- **FR-005** 回滚不还原 name。
- **FR-006** 导出含 `schema_version`；导入校验版本号，不支持 → 400。
- **FR-007** 导入重名 409；导入默认 private。
- **FR-008** team 模板可见不可改（编辑/回滚/改 visibility 均 403）。
- **FR-009** `scope` 增加 `team`；`all` 含 team。
- **FR-010** 前端：「团队共享」分组、「历史 / 共享」弹层（共享开关 + 历史 + 回滚 + 导出）、
  顶部「导入模板」。

## 成功标准

- SC-001 PATCH 一次后 versions=1 且内容是**改前**；回滚后配置回到改前、versions+1。
- SC-002 导出 → 另一账号导入 → 独立副本、内容一致；同账号再导入 409。
- SC-003 队友在 scope=team 里可见、详情 200、PATCH 403、回滚 403。
- SC-004 内置模板出参 visibility=team。

## 实测（2026-11-21）

- 后端新增 6 例（`tests/integration/test_template_versions.py`，另有 1 例在内置模板
  种子缺失时 skip），全量 **2098 passed**，覆盖率 **94.32%**；
- 前端新增 1 例（团队分组 + 无编辑入口），全量 **155 passed / 31 files**；
- 真实栈 `deploy/verify/s19_template_sharing.py` **3/3**；迁移 `0022 → 0023` 执行成功。

前端那条测试抓到一个真实缺陷：`onInspect` 只加在了卡片定义里，**「我的模板」分组的
调用处没传**——历史入口在界面上根本不存在。

## 范围外

- 模板市场（公开浏览 / 审核 / 评分 / 评论）；
- 版本对比（diff 视图）与命名版本（tag）；
- 跨实例的迁移工具（导出 JSON 已经够手工搬，自动化留待有需求）。
