# 契约：外部通知通道（018）

## 1. 端点

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/notification-channels` | member/admin | 我的通道列表 |
| POST | `/notification-channels` | member/admin | 建通道（URL 校验失败 400） |
| PATCH | `/notification-channels/{id}` | 本人 | 改 URL / 订阅 / 开关 |
| DELETE | `/notification-channels/{id}` | 本人 | 删通道（连带删投递记录），204 |
| POST | `/notification-channels/{id}/test` | 本人 | 同步发测试事件，最多等 8s |
| POST | `/notification-channels/{id}/reset-secret` | 本人 | 重置 HMAC 密钥（旧密钥立即失效） |
| GET | `/notification-channels/{id}/deliveries?limit=` | 本人 | 最近投递（默认 20，最大 100） |
| GET | `/notification-channels/event-types` | member/admin | 可订阅事件（value + 中文 label） |

## 2. 请求/响应

```json
POST /notification-channels
{ "url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx",
  "event_types": ["run_completed"], "enabled": true }

201
{ "id": "...", "kind": "webhook", "url": "...", "event_types": ["run_completed"],
  "enabled": true, "secret_configured": true, "created_at": "...", "updated_at": "..." }
```

**`secret` 永不出现在任何响应里**；只有 `secret_configured: true`。

## 3. 错误矩阵

| 条件 | 状态 | code | message |
|---|---|---|---|
| 未认证 | 401 | `UNAUTHORIZED` | 未登录 |
| viewer 调任一端点 | 403 | `FORBIDDEN` | 角色不足 |
| 通道不存在 / 不是自己的 | 404 | `NOT_FOUND` | 通知通道不存在 |
| URL 非 http(s) 或私网/保留地址 | 400 | `INVALID_INPUT` | 该地址不允许使用：… |
| 未知事件类型 | 400 | `INVALID_INPUT` | 未知事件类型：… |
| `kind` 不是 webhook | 422 | `VALIDATION_ERROR` | 字段校验失败 |
| url 长度越界 | 422 | `VALIDATION_ERROR` | 字段校验失败 |

## 4. 投递请求契约（接收端实现指引）

```http
POST <channel.url>
Content-Type: application/json; charset=utf-8
User-Agent: AgentWorkbench-Webhook/1.0
X-AW-Event: run_completed
X-AW-Delivery: <delivery uuid>
X-AW-Timestamp: <unix 秒>
X-AW-Signature: sha256=<hex>

{"delivery_id":"...","notification_id":"...","event":"run_completed",
 "title":"...","request_id":"...","occurred_at":"2026-11-21T08:00:00+00:00",
 "payload":{...}}
```

### 签名算法（接收端复算）

```
expected = "sha256=" + hex(HMAC_SHA256(secret, f"{X-AW-Timestamp}.{raw_body}"))
```

- 用**原始 body 字节**参与计算（先 JSON 解析再序列化会导致不一致）；
- 建议拒绝 `|now - timestamp| > 300s` 的请求（防重放）；
- `X-AW-Delivery` 是幂等键：**at-least-once**，超时重试可能重复投递，
  接收端按它去重（research R-04）。

## 5. 重试与状态

| 结果 | 是否重试 | 最终 status |
|---|---|---|
| 2xx | 否 | `succeeded`（attempts=1） |
| 429 / 5xx / 超时 / 连接失败 | 是（2s → 8s → 30s） | 3 次后 `failed` |
| 其他 4xx | 否（对方明确拒绝） | `failed`（attempts=1） |
| 通道被禁用/删除 | 否 | `failed`（error 说明「投递取消」） |

`pending` 表示「还会再试」，不是「正在发」。
