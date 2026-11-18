# Feature 006 API Contracts: 运行自动重试

> **Branch**: `feat/006-run-retry` | **Date**: 2026-11-18 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

本特性**不新增端点**。契约变更集中在三处：既有端点的响应体新增字段、
手动重试的成本语义变更、新增一个 WebSocket 事件。

---

## 1. `GET /runs/{id}` 响应体新增字段

```jsonc
{
  "id": "…",
  "status": "retrying",            // 新增取值：retrying
  "attempt": 2,                    // 新增：第几次尝试（首次为 1）
  "next_retry_at": "2026-11-18T09:00:04+00:00",  // 新增：下次重试时刻（非重试中为 null）
  "retry_history": [               // 新增：历次失败（无则为 []）
    {
      "attempt": 1,
      "error": "LLM 网关连接失败：Connection refused",
      "at": "2026-11-18T09:00:00+00:00"
    }
  ],
  "cost_cents": 55,                // 语义变更：跨重试累计，不再因重试重置
  "…": "其余字段不变"
}
```

**约束**：

- `status` 取值集合从 7 个扩到 8 个，前端必须处理未知值降级（显示原始字符串）
- `attempt` / `next_retry_at` / `retry_history` 对 006 之前创建的 Run 有默认值
  （`1` / `null` / `[]`），**不会**因迁移变成 null 而破坏前端
- `cost_cents` 的口径变更只影响「重试之后」，历史数据不回算

## 2. `POST /runs/{id}/retry`（手动重试）—— 行为变更

**外部契约不变**：路径、方法、状态码（202 + `{"status": "queued"}`）、
错误码（409 非 failed/timeout、404 不存在、403 非本人）全部保持。

**内部行为变更**：

| 字段 | 006 之前 | 006 之后 |
|---|---|---|
| `cost_cents` / `cost_micros` | 重置为 0 | **保留累计** |
| `error` | 置 NULL | 置 NULL（不变） |
| `current_step` | 重置为 0 | 重置为 0（不变） |
| `attempt` | （不存在） | +1 |

> 这是 spec FR-020 刻意允许的变更：契约指路径/状态码/响应体**结构**，
> 而响应体里的成本数字本来就该反映真实支出（spec FR-013/FR-014）。

## 3. 新增 WebSocket 事件：`run_retrying`

```jsonc
{
  "type": "run_retrying",
  "run_id": "…",
  "attempt": 2,                 // 即将开始的是第几次
  "max_attempts": 3,
  "wait_seconds": 3.42,         // full jitter 后的实际等待（秒，保留两位小数）
  "error": "LLM 网关连接失败：…",  // 触发本次重试的失败原因
  "next_retry_at": "2026-11-18T09:00:03+00:00"
}
```

**语义**：一次执行失败且将自动重试时推送。前端据此把状态徽章切成「重试中」
并显示倒计时。

**与既有事件的关系**：`run_failed` 仍在重试耗尽时推送（终态才推送），
`run_retrying` 是它的「非终态前奏」。两者不会对同一次失败同时推送。

## 4. 错误码（既有，未变）

| 场景 | 码 | 文案 |
|---|---|---|
| 重试非 failed/timeout | 409 | `仅 failed/timeout 状态可重试` |
| 非本人资源 | 404 | `运行不存在`（同措辞，防存在性泄露） |
| 瞬时失败重试中到点执行 | — | 无错误码：消息被静默丢弃（FR-011），不入账 |

## 5. 审计动作

| 动作 | 触发点 | detail |
|---|---|---|
| `run_retry`（既有） | 手动重试 | `{"from_status": "failed"}` |
| `run_auto_retry`（新增） | 自动重试调度 | `{"from_status": "running", "attempt": 1, "max_attempts": 3, "wait_seconds": 3.42, "error": "…"}` |

`run_auto_retry` 与 `run_retry` 分开：一个是系统行为、一个是用户行为，
混在一起会让「用户重试了多少次」这个统计失真。

## 6. 配置项（`deploy/.env.example` 同步）

| 变量 | 默认 | 说明 |
|---|---|---|
| `RUN_MAX_ATTEMPTS` | `3` | 总尝试次数（含首次） |
| `RUN_RETRY_BASE_SECONDS` | `2` | 退避基准秒数 |
| `RUN_RETRY_CAP_SECONDS` | `60` | 退避封顶秒数 |

## 7. 前端消费约定

- 状态徽章：`retrying` 用琥珀色 + 「重试中」，与 `failed`（红）明确区分
- 倒计时：由 `next_retry_at` 本地计算，不需要轮询；WS 事件到达时重置
- 重试历史：详情页「执行」区块下方折叠列表，按 attempt 倒序
- 未知状态降级：`STATUS_LABEL[status] ?? status`
