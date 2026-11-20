# Data Model: 016 错误码语义统一

**本特性不涉及任何数据模型变更**：没有新表、没有新列、没有迁移。

记录这一点是因为「错误码」容易被误认为需要状态字段。实际上它完全是
响应层语义，判据全部来自既有列：

| 判据 | 来源列 | 已有 |
|---|---|---|
| 需求相关人 | `requests.owner_id`、`requests.assignee_id` | 001 / 007 |
| 项目成员 | `project_members(project_id, user_id)` | 010 |
| Agent / 组 / 规则归属 | `agents.owner_id`、`agent_groups.owner_id` | 001 |
| 内置模板 | `agent_templates.owner_id IS NULL` | 005 |
| 外部工具归属 | `tool_installations.owner_id`、`external_credentials` | 003 |

因此：
- **无 Alembic 迁移**（不需要 `0017_*`）
- **无索引变更**
- 回滚 = revert 代码，不涉及数据恢复

## 状态机

不涉及。错误码不参与任何状态迁移。
