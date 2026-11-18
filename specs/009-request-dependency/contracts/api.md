# API Contract: 009 需求依赖编排

按关注点分三节：§1 依赖边端点（新），§2 既有端点的契约变更（assign 门禁 /
详情派生字段 / 调度路径），§3 通知与前端落点。

## 1. 依赖边端点（新，挂在 `/requests/{id}/dependencies`）

### 1.1 端点清单

| 方法 | 路径 | 谓词 | 响应 |
|---|---|---|---|
| POST | `/requests/{id}/dependencies` | owner-or-assignee of `{id}` | 201 `DependencyEdgeOut` |
| GET | `/requests/{id}/dependencies` | owner-or-assignee of `{id}` | 200 `DependencyEdgeList` |
| DELETE | `/requests/{id}/dependencies/{dependency_id}` | owner-or-assignee of `{id}` | 204 |
| GET | `/requests/{id}/dependents` | owner-or-assignee of `{id}` | 200 `DependencyEdgeList`（反向） |

### 1.2 判定顺序（POST，加边）

1. 认证 → 401；角色非 member/admin → 403（viewer 无写入口，同既有）。
2. schema：`dependency_id` 必填 uuid → 422。
3. `{id}`（被依赖方）归属校验：不存在或非 owner-or-assignee → **404**
   （不暴露存在性，同 007 纪律）。
4. 自依赖（`dependency_id == {id}`）→ **400 INVALID_INPUT**。
5. `dependency_id` 目标存在性：不存在 → **404**。**不做可见性校验**
   （R-05：内部协作，「B 等隔壁团队的 A」是核心场景）。
6. 重复边（已存在同向边）→ **409 CONFLICT**。
7. 成环检测（R-04 BFS）：从 `dependency_id` 沿既有边可达 `{id}` →
   **409 CONFLICT**，消息「会形成循环依赖」。
8. 落库 + 审计 `request_dependency_add`（detail 带目标标题快照）→ 201。

### 1.3 判定顺序（DELETE）

1–3 同 POST。边不存在 → 404（不区分「没连过」与「连的是别的」）。
删边写审计 `request_dependency_remove`（同样带标题快照——目标可能
紧接着被删，快照是唯一留痕）。

### 1.4 行为用例表

| # | 场景 | 请求 | 响应 | 断言 |
|---|---|---|---|---|
| D-1 | 加边 | POST `{dependency_id: A}` | 201 | `dependency_title/status` 随响应返回 |
| D-2 | 自依赖 | POST `{dependency_id: B 自身}` | 400 | `code=INVALID_INPUT` |
| D-3 | 目标不存在 | POST 随机 uuid | 404 | 不暴露存在性 |
| D-4 | 重复边 | 同 D-1 再来一次 | 409 | `code=CONFLICT` |
| D-5 | 成环 | A→B 已存在，给 A 加「依赖 B」 | 409 | 消息含「循环依赖」 |
| D-6 | 传递成环 | A→B→C 已存在，给 A 加「依赖 C」 | 409 | BFS 抓到二跳环 |
| D-7 | 删边 | DELETE | 204 | 再 GET 列表为空 |
| D-8 | 删不存在的边 | DELETE 随机 uuid | 404 | |
| D-9 | 他人需求加边 | 对陌生人的需求 POST | 404 | |
| D-10 | viewer 加边 | viewer token | 403 | |
| D-11 | 经手人可删边 | assignee DELETE | 204 | R-06：被 409 挡住的人能自助 |
| D-12 | 反向视图 | GET `/dependents` | 200 | 含 B（标题+状态） |

## 2. 既有端点契约变更

### 2.1 `POST /requests/{id}/assign`（分配门禁，FR-004）

判定顺序在**步骤 3「目标校验」之前**插入 2.5：

1. 归属行锁 → 2. 配额 → 2.5 **依赖门禁** → 3. 幂等状态 → 4. 目标校验 → 5. 策略展开 → 建 Run。

> 顺序理由：门禁在配额之后（配额是更基础的准入）、目标校验之前
> （目标解析可能更贵，且「被挡住」与「目标错」是两个独立错误，
> 先报更根本的那个）。

- 查询：`§data-model §5` 的半连接，一次。
- 未完成前置非空 → **409 CONFLICT**，消息逐条：
  `"有 2 个前置需求未完成：Q4 官网改版（执行中）、数据清洗（待处理）"`。
  超过 5 条时截断为 `"…等 7 个"`（消息不是清单 UI，详情弹层才是）。
- `assignee_type=user`（007 拖员工转交）**不查门禁**（FR-005）。
- 重试路径不查（R-08）。

### 2.2 `GET /requests/{id}`（详情派生字段）

响应新增 `dependencies`（边数组，含标题/状态/terminal）与 `blocked`（布尔）。
**既有字段一个不改**（SC-005）。

### 2.3 `GET /requests`（列表）

响应每项新增 `blocked`（布尔，§data-model §4 的两查询算法）；
**不返回** `dependencies` 明细。

### 2.4 定时调度（004，FR-009）

`_fire_and_assign` 调 assign 前先查门禁；未满足 → 跳过分配：
- trigger `result=fired`，`reason="前置未完成，未自动分配"`；
- 需求留在 `created`（无 Run）；
- **不**走既有 AppError→failed 路径（那条路连需求一起回滚，R-07）。

### 2.5 错误码速查（009 新增面）

| 码 | 场景 |
|---|---|
| 400 | 自依赖 |
| 404 | 被依赖方/前置目标不存在；删不存在的边 |
| 409 | 重复边；成环；分配被未完成前置挡下 |
| 403 | viewer 用边端点 |

## 3. 通知与前端落点

### 3.1 通知（FR-007/008）

- type `dependency_ready`；payload `{dependency_id, dependency_title, dependency_status}`。
- 受众：后续需求 owner + assignee（去重）；后续已终态/running → 不发。
- 前端铃铛文案按 `dependency_status` 三分：`completed` →「前置需求已完成，
  可以启动了」；`failed` →「前置需求已失败——请确认后手动启动」；
  `cancelled` →「前置需求已取消——请确认后手动启动」。

### 3.2 前端契约

- 详情弹层新增「前置需求」区块：边列表（标题 + 状态徽章 + terminal 标记）
  + 添加器（搜索/下拉选需求，默认带出标题）+ 每条边的移除按钮；
  被挡住时区块顶部一行提示「有 N 个前置未完成，暂不能分配执行」。
- 需求卡片：元信息行前置徽章（图标 + 未完成数量），`title` 提示逐条标题
  （US4）；无未完成前置不渲染。
- 分配被 409 挡下：既有错误横幅直接展示后端消息（逐条阻塞项），不新造 UI。
- 铃铛通知中心：`dependency_ready` 走既有 `NotificationItem`，
  TYPE_META 加一行（图标 `clock`，标签「前置就绪」）。
