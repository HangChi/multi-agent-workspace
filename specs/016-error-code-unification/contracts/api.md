# Contracts: 016 错误码语义统一

## §1 判定顺序（任何端点都按这个顺序）

```
认证（401）→ 角色（403）→ 资源存在性/归属（404）→ 字段与动作权限（403）→ 业务校验（409/422/429）
```

顺序本身就是语义的一部分：
- **认证先于一切**：未认证不谈资源，一律 401。
- **角色先于资源**：viewer 请求任何写端点都是 403，哪怕资源不存在——
  否则 viewer 可以用状态码探测资源是否存在。
- **存在性/归属先于字段权限**：先判「你和它有没有关系」，再判「你能不能改这个字段」。

## §2 统一口径

> 对「与该资源没有任何关系」的访问者，一律 `404 NOT_FOUND`，
> 且 `message` 与「资源确实不存在」**逐字相同**。

## §3 端点矩阵

「非相关人」列是本次改动后的**期望值**。标 🔁 的是本次从 403 改成 404 的。

### 3.1 需求（001 / 007 / 008 / 009）

相关人 = **创建者 ∪ 当前接收人 ∪ 项目成员**（010 的 `allow_member` 路径）。

| 端点 | 非相关人 | 参与者但缺权限 |
|---|---|---|
| `GET /requests/{id}` | 404 🔁 | — |
| `PATCH /requests/{id}` | 404 🔁 | 403（assignee 改 `project_id`） |
| `DELETE /requests/{id}` | **无此端点（405）** —— 需求不可物理删除 | — |
| `POST /requests/{id}/assign` | 404 🔁 | 403（非创建者分配） |
| `POST /requests/{id}/accept` | 404（007 既有） | 409（重复接收） |
| `POST /requests/{id}/reject` | 404（007 既有） | 409 |
| `POST /requests/{id}/attachments` | 404 🔁 | — |
| `GET /requests/{id}/attachments/{key}` | 404 🔁 | — |
| `POST /requests/{id}/dependencies` | 404 🔁 | 409（成环）/ 422 |
| `GET /requests/{id}/dependencies` | 404 🔁 | — |
| `DELETE /requests/{id}/dependencies/{dep_id}` | 404 🔁 | 409 |
| `GET /requests/{id}/dependents` | 404 🔁 | — |
| `GET /requests`（列表） | 只含相关项（不报错） | — |

### 3.2 Agent（001 / 005）

| 端点 | 非 owner | 内置模板 |
|---|---|---|
| `GET /agents` | 只返回自己的 | — |
| `POST /agents` | — | — |
| `PATCH /agents/{id}` | 404 🔁 | — |
| `DELETE /agents/{id}` | 404 🔁 | — |
| `POST /agents/{id}/archive` | 404 🔁 | — |
| `POST /agents/{id}/clone` | 404 🔁 | — |
| `POST /agents/from-template/{tid}` | 模板不存在 404 | 允许（内置可克隆） |

### 3.3 Agent 组（001）

| 端点 | 非 owner |
|---|---|
| `GET /groups/{id}` | 404 🔁 |
| `PATCH /groups/{id}` | 404 🔁 |
| `DELETE /groups/{id}` | 404 🔁 |
| `POST /groups/{id}/members` | 404 🔁（组非本人）；加入**他人 Agent** 也是 404 🔁 |
| `PATCH /groups/{id}/members` | 404 🔁 |
| `DELETE /groups/{id}/members/{agent_id}` | 404 🔁 |

### 3.4 出网规则（002）

| 端点 | 非 Agent owner |
|---|---|
| `GET /agents/{id}/egress-rules` | 404 🔁 |
| `POST /agents/{id}/egress-rules` | 404 🔁 |
| `DELETE /agents/{id}/egress-rules/{rule_id}` | 404 🔁 |

### 3.5 外部工具（003）

| 端点 | 非本人 |
|---|---|
| `POST /marketplace/installations/{id}/upgrade` | 404 🔁 |
| `POST .../credentials/{cid}/revoke` | 404 🔁 |
| `POST /agents/{id}/external-tool-grants` | Agent 非本人 404 🔁 |
| `GET /agents/{id}/external-tool-grants` | Agent 非本人 404 🔁 |
| `DELETE /agents/{id}/external-tool-grants/{gid}` | 404 🔁 |

### 3.6 项目与调度（008 / 009 / 010 / 004）

| 端点 | 非相关人 | 参与者但缺权限 |
|---|---|---|
| `GET /projects/{id}` | 404（010 既有） | — |
| `PATCH /projects/{id}` | 404（010 既有） | 403（成员非 owner 改元数据） |
| `DELETE /projects/{id}` | 404（010 既有） | 403 |
| `POST /projects/{id}/members` | 404（010 既有） | 403（成员非 owner） |
| `GET /schedules/{id}` | 404（004 既有） | — |
| `PATCH /schedules/{id}` | 404（004 既有） | 403（admin 只读他人） |

## §4 必须保持 403 的白名单（FR-007）

这些**不要**顺手改成 404：

| 场景 | 位置 | 理由 |
|---|---|---|
| viewer 调 member/admin 端点 | `api/deps.py` `require_role` | 角色不足，与资源无关 |
| 项目成员做 owner 动作 | `project_member_service.py:43` | 他知道项目存在 |
| 内置模板编辑/删除 | `agent_template_service.py:145/165` | 模板全体可见 |
| admin 修改他人调度 | `schedule_service.py:179` | admin 本就能读 |
| assignee 改 `project_id` | `request_service.py:361` | 他经手该需求 |
| 登录失败 / 账号禁用 | `auth_service.py:148-152` | 与资源无关 |

## §5 响应体形状

两种响应必须**同构**：

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "需求不存在"
  }
}
```

- 「存在但不归你」与「真的不存在」的 JSON **完全相等**（SC-003）。
- message 不得出现「无权限」「不属于你」等暗示存在性的措辞。

## §6 不改的部分

- `401`：未认证，语义不变。
- `422`：非法 UUID / schema 校验（`deps._resource_id`）。
- `409`：状态冲突（重复接收、成环、终态不可迁移）。
- `429`：配额与限流。
- WebSocket close code `1008`：不适用 HTTP 状态码。
