# Data Model: 013 s12 发现修复

零 schema 变更（纯前端 + 验收脚本）。改动清单：

| 文件 | 改动 | 对应 FR |
|---|---|---|
| `frontend/src/components/layout/nav.ts` | `/projects` 项加 `memberOnly: true` | FR-001 |
| `frontend/src/pages/ProjectsPage.tsx` | 列表查询 `scope: 'mine'` → `'relevant'`（queryKey 同步） | FR-002 |
| `frontend/src/components/request/RequestForm.tsx` | 项目选择器查询切 `relevant` | FR-003 |
| `frontend/src/components/request/DependencySection.tsx` | 新增 `onChanged?: () => void`，增删边 onSuccess 调用 | FR-004 |
| `frontend/src/pages/BoardPage.tsx` | `refreshDetail` 助手 + 弹层 DependencySection 接 `onChanged` | FR-005 |
| `deploy/verify/s12_browser_walkthrough.py` | `_open_detail` 等详情拉取；B1 断言改对话框级 | FR-006 |

## 权限正交性（切 relevant 不破坏的门）

- 项目管理页的写操作（改名/归档/删除/成员管理）由 `MemberSection` 的
  `isOwner`（`user.id === project.owner_id`）与 ProjectsPage 行内按钮的
  owner 判断把守——与列表 scope 正交。
- 建需求的 `project_id` 归属校验走后端 `load_active_owned_project`
  （010 已扩展为 owner ∪ 成员）。

## 明确不做

- ❌ 后端零改动（三项发现全是前端）。
- ❌ 不改 s12 的验收对象（断言不动，只修等待与定位器）。
- ❌ 不给项目页加 scope 切换 tab（R-03）。
