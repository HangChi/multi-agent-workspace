# Quickstart：外部通知通道（018）真实栈验收

前置：全栈已起、邀请码可用、容器可出网（2026-11-21 实测 httpbin 可达）。

## 1. 一条命令跑完五组场景

```bash
backend/.venv/bin/python deploy/verify/s14_notification_webhook.py \
  --base http://localhost:8080 --invite <INVITE>
```

期望输出：

```
✅ A1 建通道 201 且不回显 secret
✅ A2 私网地址保存即 400
✅ B1 事件触发真实投递（succeeded / 1 次）
✅ C1 禁用后不再产生投递
✅ D1 未订阅的事件不建投递
合计 5 场景：5 通过 / 0 失败
```

## 2. 手工验签（想确认签名真的对）

脚本用的是 httpbin（不验签），要验签名就找一个愿意回显的接收端，例如
`https://httpbin.org/anything`（它会把收到的 header 原样返回）：

```bash
BASE=http://localhost:8080/api/v1
TOKEN=<你的 access token>
SECRET_HINT=<通道 secret 不可见——见下方说明>

# 建一个指向 /anything 的通道并立刻测试
CID=$(curl -s -X POST "$BASE/notification-channels" \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"url":"https://httpbin.org/anything","event_types":[]}' | jq -r .id)

curl -s -X POST "$BASE/notification-channels/$CID/test" \
  -H "authorization: Bearer $TOKEN" | jq
```

`secret` 不出接口。要复算签名，在库里取（运维操作）：

```bash
docker exec aw-postgres psql -U aw -d agent_workbench -tA \
  -c "select secret from notification_channels where id='$CID';"
```

然后按 `contracts/api.md` §4 的公式复算 body + `X-AW-Timestamp`。

## 3. 用真实 IM 机器人验一条

企业微信群机器人最省事：群设置 → 添加机器人 → 复制 webhook URL，把它填进
设置页 →「通知渠道」→ 新建 → 「发送测试」，群里应立刻收到一条 JSON。

> 机器人只显示文本的话，收到的会是原始 JSON——这是正常现象（我们发的是标准
> webhook POST，不是各家 IM 的专有格式）。要转成卡片消息需要中间层，属于
> 后续特性。

## 4. 排查

| 现象 | 先看 |
|---|---|
| 保存时报「不允许使用」 | URL 是内网/保留地址；纯内网部署需 `NOTIFICATION_ALLOW_PRIVATE_HOSTS=true` |
| 通道存在但没有投递记录 | 事件没发生，或 `event_types` 订阅不匹配（空数组=全部） |
| 投递 `failed` 且 attempts=1 | 对方回了 4xx：URL 错、密钥不匹配、接口路径变了 |
| 投递 `failed` 且 attempts=3 | 对方持续 5xx 或超时；看 `last_error` |
| `pending` 一直不结束 | worker 没起来或队列堵了：`docker compose logs aw-worker`、`/metrics` 的队列深度 |

## 5. 清理与回滚

```bash
# 删掉验收造的账号（dry-run 默认）
backend/.venv/bin/python deploy/verify/cleanup_test_data.py
```

代码回滚：`alembic downgrade 0017` 会删两张表（投递历史随之丢失，可接受）。
