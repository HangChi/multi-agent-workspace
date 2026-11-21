# Quickstart：依赖图谱与自动启动（021）

## 1. 图谱（只读，随时可验）

```bash
BASE=http://localhost:8080/api/v1
TOKEN=<access token>
A=$(curl -s -X POST "$BASE/requests" -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"title":"链A"}' | jq -r .id)
B=$(curl -s -X POST "$BASE/requests" -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"title":"链B"}' | jq -r .id)
curl -s -X POST "$BASE/requests/$B/dependencies" -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d "{\"dependency_id\":\"$A\"}" > /dev/null

curl -s "$BASE/requests/$A/dependency-graph" -H "authorization: Bearer $TOKEN" \
  | jq '{topo_order, critical_path, nodes: [.nodes[] | {title, unmet_count}]}'
```

期望：`topo_order` 与 `critical_path` 都是 `[A, B]`；B 的 `unmet_count=1`。

## 2. 自动启动（真实执行需要跑完前置）

```bash
AGENT=$(curl -s -X POST "$BASE/agents" -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"name":"自动执行","system_prompt":"简短回答","model":"Deepseek-V4.1-Flash","max_steps":2,"cost_limit_cents":1000,"tools":[]}' | jq -r .id)

# 让 B 在前置完成后自动交给 AGENT
curl -s -X PATCH "$BASE/requests/$B" -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d "{\"auto_start\":true,\"auto_agent_id\":\"$AGENT\"}" | jq '{auto_start, auto_agent_id}'
```

然后把 A 真的跑完（拖到任意 agent，等它 `completed`）。B 应自动进入执行：
`GET /requests/$B` 的状态变成 `assigned`/`running`，并多出一个 Run。

查看是否触发：

```bash
docker logs aw-worker --tail 50 | grep -E "auto_started|auto_start_rejected|auto_start_enqueued"
```

- `auto_start_enqueued`：settle 侧已投递；
- `auto_started`：任务分配成功；
- `auto_start_rejected`：被门禁拒绝（看 code：`RATE_LIMITED` 是配额、
  `CONFLICT` 是状态不允许），需求保持 pending 等你手动启动。

## 3. 排查

| 现象 | 先看 |
|---|---|
| 图谱 `critical_path` 为空 | 没有下游（只有根节点），或 `has_cycle=true` |
| `truncated=true` | 下游超过 300 节点，先用项目过滤再查 |
| 打开开关后没自动跑 | worker 是否在跑；`auto_agent_id` 是否属于**需求创建者**（别人的 agent 会被 404 拒绝） |
| 自动启动被拒绝 | `docker logs aw-worker | grep auto_start_rejected`，看 code |

## 4. 回滚

`alembic downgrade 0020` 会删两列（配置丢失，已启动的 Run 不受影响）。
