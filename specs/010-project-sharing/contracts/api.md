# API Contract: 010 项目共享与成员

按关注点分三节：§1 成员管理端点（新），§2 既有端点的口径扩展
（projects/requests），§3 通知与前端落点。

## 1. 成员管理端点（新，挂在 `/projects/{id}/members`）

### 1.1 端点清单

| 方法 | 路径 | 谓词 | 响应 |
|---|---|---|---|
| POST | `/projects/{id}/members` | project owner | 201 `ProjectMemberOut` |
| GET | `/projects/{id}/members` | owner ∪ 成员 | 200 `ProjectMemberList` |
| DELETE | `/projects/{id}/members/{user_id}` | project owner | 204 |

### 1.2 判定顺序（POST）

1. 认证 → 401；角色非 member/admin → 403。
2. schema：`user_id` 必填 uuid → 422。
3. 项目存在性 + **owner 判定**：不存在或非本人创建 → 404（成员管理是
   owner 特权，且不暴露存在性；是成员但非 owner → **403**——
   与「他人项目 404」区分：成员知道项目存在，403 说清「你不够权」）。
4. 目标用户存在 + `status='active'`：不存在 → 404；已停用 → 400
   （在职口径，同分派选人，R-08）。
5. 重复成员（PK 冲突）→ 409。
6. 插入 + 通知 `project_member_added`（发给**被加者**）+ 审计
   `project_member_add` → 201。

### 1.3 判定顺序（DELETE）

1–3 同 POST（owner 判定）。
4. 自移除（`user_id == owner 本人`）→ **409**（R-04：无主项目是脏状态，
   消息说明可删项目、移交在后续）。
5. 成员关系不存在 → 404。
6. 删除 + 通知 `project_member_removed`（发给被移出者）+ 审计 → 204。
7. **删项目路径不发通知**（R-05）。

### 1.4 行为用例表

| # | 场景 | 请求 | 响应 | 断言 |
|---|---|---|---|---|
| M-1 | 加成员 | POST `{user_id: 同事}` | 201 | 被加者铃铛有 `project_member_added` |
| M-2 | 重复加 | 同 M-1 再来 | 409 | PK 冲突友好错误 |
| M-3 | 停用用户 | POST 停用账号 | 400 | 在职口径 |
| M-4 | 目标不存在 | POST 随机 uuid | 404 | |
| M-5 | 成员加人 | 成员 token POST | 403 | 成员管理是 owner 特权 |
| M-6 | 他人项目加人 | 非 owner 非成员 POST | 404 | 不暴露存在性 |
| M-7 | 自移除 | owner DELETE 自己 | 409 | 消息含「不能退出」 |
| M-8 | 移除不存在 | DELETE 随机 uuid | 404 | |
| M-9 | 成员列表 | owner/成员 GET | 200 | 含 owner（`is_owner` 标记）+ 成员 |
| M-10 | 成员看列表 | 成员 GET | 200 | 可见性是成员级，管理才是 owner 级 |
| M-11 | 他人看列表 | 陌生人 GET | 404 | |
| M-12 | 删项目级联 | DELETE project | 204 | 成员行消失；**无** removed 通知 |

## 2. 既有端点的口径扩展（零回归：只扩「相关人员」集合）

### 2.1 `GET /projects`（列表）

- `scope=relevant`（默认）= 我创建的 ∪ **我是成员的** ∪ 派给我需求所属的
  （FR-004）。
- 响应新增 `member_count`（含 owner）；`request_count` 语义升级为
  「按调用人可见口径」（R-06）。
- `scope=mine` 不变（仅我创建的）。

### 2.2 `GET /projects/{id}`（详情）

- 谓词 owner → **owner ∪ 成员**（成员 200，陌生人仍 404）。
- 响应同 §2.1 加 `member_count`。

### 2.3 `GET /requests`（列表）

- `scope=relevant`（默认）= owner ∪ assignee ∪ **其项目的成员**
  （半连接：`EXISTS (SELECT 1 FROM project_members WHERE project_id =
  requests.project_id AND user_id = me)`）。
- `scope=mine` 不变。`?project_id=` 过滤的校验随之扩展：项目对调用人
  可见（owner ∪ 成员 ∪ 含我可见需求）才放行（`project_visible_to_user`
  加一肢「成员」——009 的同一函数，避免下拉与过滤口径分叉）。

### 2.4 `GET /requests/{id}`（详情）

- 谓词 owner-or-assignee → **+ 项目成员**（403 → 200）。
- **错误码不变**：非相关人员仍 403（FR-012）——扩的是集合不是码。

### 2.5 `PATCH /requests/{id}`（编辑）

- 普通字段（标题/描述/优先级/expected_output）：owner ∪ assignee ∪ 项目成员。
- `project_id` 字段：**仅创建者或该项目 owner**（R-03）——请求体含该键时
  单独判定，不满足 403（消息「仅创建者或项目创建者可变更归属」）。
- 显式 `null`（解除归属）同样受限。

### 2.6 `POST /requests/{id}/assign` 与 009 依赖边端点

- assign（agent/组）：owner ∪ assignee ∪ 项目成员；009 门禁照查
  （依赖与共享正交——成员拖项目内需求照样被未完成前置挡下）。
- 依赖边 POST/DELETE/GET：同一谓词扩展。
- **accept/reject 不变**（仅当前 assignee，R-07）。

### 2.7 `POST /requests`（创建）

- `project_id` 校验：active 且（我是 owner **或成员**）→ 否则
  他人项目 404 / 已归档 400（FR-007，同 009 的 404/400 分工）。

### 2.8 错误码速查（010 新增面）

| 码 | 场景 |
|---|---|
| 404 | 项目/成员目标不存在；他人项目（非 owner 非成员）的成员操作 |
| 403 | 成员做 owner 动作（加人/踢人/改项目）；非创建者/项目 owner 改需求归属 |
| 409 | 重复成员；owner 自移除 |
| 400 | 停用用户被加为成员 |

## 3. 通知与前端落点

### 3.1 通知

- `project_member_added` / `project_member_removed`，**只发给对方本人**。
- payload：`{project_id, project_name}`（前端文案「你已被加入/移出项目 X」）。
- 删项目不发（R-05）。
- 铃铛 TYPE_META 各加一行（图标 `users`，标签「成员变动」——两种类型
  共用一个 meta，文案按 type 二分）。

### 3.2 前端契约

- 项目管理页：每个项目行展开/显示成员区（owner 徽章 + 成员列表 +
  添加器（`GET /users/options` 数据源）+ 移除按钮）；owner 才见操作入口，
  成员只读列表（FR-011）。
- 项目卡片/列表：「我的」/「共享的」徽章区分（`owner_id === 当前用户`）。
- 看板项目下拉：scope=relevant 自动含共享项目（无前端改动——
  后端口径扩展即可，这是把口径收敛在后端的收益）。
- 建需求表单项目选择器：自动含共享项目（同上）。
- 需求详情/卡片的项目徽章：共享项目可加一个小「共享」标记（可选，
  有 member_count > 1 时）。
