# 契约：项目所有权移交（017）

## 1. 端点

### `POST /api/v1/projects/{project_id}/transfer`

**权限**：当前 `owner_id` 本人，且调用者是 member/admin 角色（viewer 403，走既有
`MemberUser` 依赖）。

**请求体**

```json
{
  "new_owner_id": "uuid",
  "keep_as_member": true
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `new_owner_id` | uuid | 是 | 接手人；必须是该项目现有成员 |
| `keep_as_member` | bool | 否（默认 `true`） | 原 owner 是否保留为成员 |

**成功响应**：`200 OK`，body 为 `ProjectOut`（结构与 `GET /projects/{id}` 完全一致，
`owner_id` 已是新值，`member_count` 已按新成员表计算）。

**为什么是 200 而不是 201**：没有创建新资源，只是既有资源的归属变更（同 PATCH 语义）。

## 2. 错误矩阵（判定顺序即自上而下）

| 序 | 条件 | 状态 | code | message |
|---|---|---|---|---|
| 1 | 未认证 | 401 | `UNAUTHORIZED` | 未登录 |
| 2 | 调用者角色是 viewer | 403 | `FORBIDDEN` | 只读角色不能移交项目 |
| 3 | 项目不存在 | 404 | `NOT_FOUND` | 项目不存在 |
| 4 | 调用者不是 owner 且不是成员 | 404 | `NOT_FOUND` | 项目不存在 |
| 5 | 调用者是成员但非 owner | 403 | `FORBIDDEN` | 仅项目创建者可移交所有权 |
| 6 | `new_owner_id` == 调用者 | 409 | `CONFLICT` | 不能移交给自己 |
| 7 | 目标用户不存在 | 404 | `NOT_FOUND` | 用户不存在 |
| 8 | 目标用户已停用 | 400 | `INVALID_INPUT` | 用户已停用，不能接收项目 |
| 9 | 目标角色是 viewer | 409 | `CONFLICT` | 只读角色不能接收项目 |
| 10 | 目标不是项目成员 | 409 | `CONFLICT` | 目标用户不是项目成员，请先加为成员 |
| 11 | 目标名下已有同名项目 | 409 | `CONFLICT` | 目标用户已有同名项目「X」，请先改名或换人 |
| 12 | `new_owner_id` 格式非法 | 422 | `VALIDATION_ERROR` | 字段校验失败（FastAPI 默认） |

**序 3–5 是 016 矩阵的机械应用**：非相关人一律 404 且 message 与「不存在」逐字相同；
参与者缺动作权限 403。**序 7 的 404 与序 8 的 400 都是既有先例**：010 的
`add_member` 就是这么回目标的（`用户不存在` 404 / `用户已停用，不能添加为项目成员`
400），理由见 research R-03。

**序 11 的 message 必须带项目名**：这是本端点唯一「告诉用户怎么修」的 409，
前端直接透出该 message（012 的 ErrorBanner 字符串直通已保证这一点）。

## 3. 副作用（同一事务）

| 副作用 | 说明 |
|---|---|
| `projects.owner_id` | 更新为新 owner |
| `project_members` | 删除新 owner 的成员行（若存在）；`keep_as_member=true` 时插入原 owner 行 |
| `notifications` | 给新 owner 插一条 `project_ownership_received`，`title` = 项目名，`payload = {project_id, project_name, previous_owner_id}` |
| `audit_logs` | `action=project_transfer`，`target_type=project`，`detail={from_user_id, to_user_id, keep_as_member, project_name}` |

事务边界：从加锁读项目到 commit 是一次事务；任一步失败整体回滚（尤其保证
「同名冲突时归属不变」）。

## 4. 与既有端点的关系

| 端点 | 变化 |
|---|---|
| `GET /projects?scope=mine` | 移交后：原 owner 少一条，新 owner 多一条 |
| `GET /projects?scope=relevant` | `keep_as_member=true` 时原 owner **仍能看到**（经成员关系）；`false` 时看不到 |
| `GET /projects/{id}/members` | 移交后成员列表：新 owner 不在列表（owner 不双写），原 owner 视 keep_as_member |
| `DELETE /projects/{id}/members/{me}` | owner 自移除仍是 409，**文案更新**（见下） |
| `PATCH /projects/{id}` | 不变。移交后原 owner 打这个端点得 403（成员做 owner 动作） |

### 变更：owner 自移除的 409 文案

```diff
- "创建者不能退出自己创建的项目（可删除项目）"
+ "创建者不能退出自己创建的项目：请先移交所有权，或删除项目"
```

既有测试若按原字符串断言，需要同步更新（tasks T0xx 内列明）。

## 5. 前端契约

- 项目卡片（`ProjectsPage`）的成员区上方增加「移交」按钮，**仅当**
  `project.owner_id === currentUser.id` 且成员数 > 1 时渲染
  （没有其他成员时移交必然失败，不给用户一条走不通的路）。
- 点击后打开对话框：
  - 成员下拉（数据源 `GET /projects/{id}/members`，排除自己）；
  - 复选框「移交后我仍保留为成员」（默认勾选）；
  - 危险按钮「确认移交」（`tone="danger"`，因为它是不可撤销的权限变更）；
  - 失败时错误消息**原样**展示后端 message（同名冲突的解释全靠它）。
- 成功后刷新项目列表与成员列表（该项目徽章可能从「我的」变「共享的」）。
