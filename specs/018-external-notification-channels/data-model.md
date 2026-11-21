# Data Model：外部通知通道（018）

## 1. 新增两张表（迁移 `0018_notification_channels`）

### `notification_channels`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | uuid PK | |
| `user_id` | uuid FK→users CASCADE | 单人资源 |
| `kind` | varchar(20) | 本期仅 `webhook`，CHECK 约束而非枚举（加 kind 不用迁移——017 的教训） |
| `url` | varchar(2048) | 保存时已规范化并做过目标校验 |
| `secret` | varchar(64) | HMAC 密钥，服务端生成，不回显 |
| `event_types` | json | 空数组 = 全部事件 |
| `enabled` | bool default true | 禁用即退订 |
| `created_at` / `updated_at` | timestamptz | |

索引：`ix_notification_channels_user_id`（列表按 user 过滤）。

### `notification_deliveries`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | uuid PK | 同时作为 `X-AW-Delivery` 幂等键 |
| `channel_id` | uuid FK→notification_channels CASCADE | 删通道即删记录 |
| `notification_id` | uuid FK→notifications CASCADE，可空 | 通知被清理后记录仍可读 |
| `event_type` | varchar(64) | 冗余存，便于通知消失后解释「送了什么」 |
| `status` | varchar(20) | `pending` / `succeeded` / `failed`（CHECK） |
| `attempts` | int | 已尝试次数 |
| `last_error` | text 可空 | ≤500 字符摘要，**不含响应体** |
| `last_attempt_at` | timestamptz 可空 | |
| `created_at` | timestamptz | |

索引：`(channel_id, created_at)`（设置页取最近投递）、`status`（排查重试积压）。

## 2. 不变式

| 不变式 | 保障 |
|---|---|
| 投递状态只能取三个值 | CHECK 约束 |
| 禁用通道不产生新投递 | `enqueue_for_notification` 只查 `enabled=True` |
| 删除通道后其投递记录一并消失 | `ON DELETE CASCADE` |
| 同一通知对同一通道只建一条投递 | 由 `enqueue_for_notification` 一次遍历创建（无唯一约束；重复调用会重复建，但当前只有一个调用点） |

## 3. 与既有表的关系

- `notifications`：不受影响（不改列、不加枚举值）。019 之前担心的枚举坑在 018 不适用
  ——本期不新增通知类型。
- `users`：新增两条 CASCADE 引用，删用户会连带删掉其通道与投递记录（符合预期：
  通道是个人配置）。

## 4. 回滚面

| 层面 | 方式 |
|---|---|
| 代码 | 回退提交 |
| 迁移 | `alembic downgrade 0017` 会 drop 两张表（**投递历史随之丢失**，属可接受：它是过程记录，不是事实账本） |
| 数据 | 无「回滚后仍需保留」的数据，故无额外处理 |
