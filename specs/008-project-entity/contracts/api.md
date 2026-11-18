# API Contract: 008 项目实体与需求归属

按关注点分两节：§1 项目端点（新），§2 需求端点的契约变更（扩展）。
每节给**判定顺序**（顺序本身是契约）与**行为用例表**。

## 1. 项目端点（新，`/api/v1/projects`）

### 1.1 端点清单

| 方法 | 路径 | 角色 | 响应 |
|---|---|---|---|
| POST | `/projects` | member/admin | 201 `ProjectOut` |
| GET | `/projects` | member/admin | 200 `ProjectList`（含 `request_count`） |
| GET | `/projects/{id}` | member/admin | 200 `ProjectOut` / 404 |
| PATCH | `/projects/{id}` | member/admin | 200 `ProjectOut` / 404 / 409 / 422 |
| DELETE | `/projects/{id}` | member/admin | 204 / 404 |

### 1.2 判定顺序（POST）

1. 认证 → 401。
2. 角色非 member/admin → 403（viewer 无任何项目入口，与 agents/groups 一致）。
3. schema 校验：`name` 必填 1..100（去空白后非空）、`description` ≤2000 → 422。
4. 同名（本人名下，含已归档项目——归档项目仍占用名字，防止「归档后再建同名」
   造成历史归属混淆）→ 409 CONFLICT。
5. 创建，写审计 `project_create`，201。

### 1.3 判定顺序（PATCH）

1. 认证/角色同上。
2. schema：两个字段都可选；`status` 仅接受 `active`/`archived`；`name` 非空时同样查重 → 409。
3. 项目不存在或非本人 → 404。
4. 若改 `name`：同步 `UPDATE requests SET project_name=:new WHERE project_id=:id`（同事务）。
5. 写审计 `project_update`（detail 带 `{fields:[...]}`，不记旧值——名称非敏感，
   且审计流已够长，同 006 对 run 字段的处理）。

### 1.4 判定顺序（DELETE）

1. 认证/角色同上。
2. 不存在或非本人 → 404。
3. 删除：`DELETE projects` → 外键把其下需求 `project_id` 置 NULL；
   service 同事务清这些需求的 `project_name`（R-04 注记）。
4. 写审计 `project_delete`（detail 带 `{request_count: N}`——删了多少条需求的归属，
   事后排查「我的需求怎么集体没项目了」就靠这个数）。

### 1.5 行为用例表

| # | 场景 | 请求 | 响应 | 断言 |
|---|---|---|---|---|
| P-1 | 建项目 | POST `{name:"官网改版"}` | 201 | `status=active`；出现在 GET 列表首位 |
| P-2 | 同名再建 | POST 同名 | 409 | `code=CONFLICT` |
| P-3 | 他人项目 | GET/PATCH/DELETE 他人 id | 404 | `code=NOT_FOUND`，不暴露存在性 |
| P-4 | viewer | 任意方法 | 403 | 无入口 |
| P-5 | 改名同步 | PATCH `{name:"新名"}` | 200 | 名下需求的 `project_name` 全部变新名 |
| P-6 | 归档后新建被拒 | PATCH `{status:"archived"}` → 建需求带该项目 | 200 后 400 | 归档不改动既有需求归属 |
| P-7 | 删除级联置空 | DELETE | 204 | 需求 `project_id/project_name` 全空；需求/Run/经手链/通知计数不变 |
| P-8 | 列表带计数 | GET `/projects` | 200 | `request_count` = 该项目下我可见的需求数（含分派给我的） |
| P-9 | 空名/超长 | POST `{name:"  "}` / 101 字符 | 422 | schema 层拦 |

## 2. 需求端点契约变更（扩展，零回归）

### 2.1 `POST /requests`（创建）

body 新增可选 `project_id: uuid | null`。
判定顺序插在既有校验之后、落库之前：

1. 既有：认证 → 403（viewer）→ 422（schema）→ 201。
2. 新增：`project_id` 非空时，校验「存在 + 属本人 + active」：
   - 不存在或属他人 → **404 NOT_FOUND**（与归属纪律一致，不暴露存在性）；
   - 已归档 → **400 INVALID_INPUT**（看得见但不能用，R-03）；
   - 通过则同时写 `project_id` 与 `project_name`（快照，R-04）。

### 2.2 `PATCH /requests/{id}`（编辑）

body 新增可选 `project_id`。**字段级权限分流**（R-05）：

- 本次请求体**不含** `project_id` 键 → 维持 007 谓词（owner-or-assignee）。
- **含** `project_id` 键 → 要求 `owner_id == 当前用户`，否则 **403 FORBIDDEN**
  （message：「仅创建者可变更需求的项目归属」）。经手人改标题/描述/优先级不受影响。
- 取值校验同 §2.1（404/400）；显式传 `null` = 解除归属（创建者特权）。

### 2.3 `GET /requests`（列表）

新增 query 参数，与既有参数正交叠加：

| 参数 | 取值 | 语义 |
|---|---|---|
| `project_id` | uuid | 该项目下的需求；项目可已归档（回看），不存在/他人 → 404 |
| `project` | `none` | `project_id IS NULL`（未归属，R-06） |

- 与 `scope=relevant|mine` 同时传时取交集（例：`scope=mine&project_id=A`
  = 我创建的 A 下需求）。
- 响应 `RequestOut` 新增 `project_id` / `project_name`（可空，默认 null）——
  **既有字段一个不改**，SC-006 的逐字段零回归据此判定。
- 列表的 `project_name` 直接读快照列，无 JOIN（R-04）。

### 2.4 `GET /requests/{id}`（详情）

响应同样新增 `project_id` / `project_name`。取值来自快照列。

### 2.5 错误码速查（008 新增面）

| 码 | 场景 |
|---|---|
| 404 | 项目不存在/属他人；需求关联的项目 id 无效 |
| 409 | 项目同名（本人名下） |
| 400 | 关联已归档项目 |
| 403 | viewer 用项目端点；非创建者改需求的 project_id |
| 422 | 项目名/描述/status 校验失败 |

### 2.6 前端契约（对应 §1/§2 的 UI 落点）

- 管理页 `/projects`：表格（名称/描述/状态/需求数/操作）+ 新建行内表单 +
  改名（行内编辑）+ 归档/恢复切换 + 删除（ConfirmDialog，文案说明「需求会保留、
  归属 cleared」）。
- 看板工具条：项目下拉（「全部项目」+ 各项目带计数 + 「未归属」选项），
  选中态进 URL `?project=<id>` / `?project=none`，刷新与分享保持。
- 建需求表单：项目选择器（下拉，默认「不归类」），仅列本人 active 项目。
- 需求卡片：项目名徽章（无归属不渲染）；详情弹层在标题徽章行显示项目名。
