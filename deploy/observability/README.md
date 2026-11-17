# 可观测性（Prometheus）

## 启动

```bash
# 指标随 API 一起暴露，Prometheus 用 profile 单独拉起
docker compose -p aw -f deploy/docker-compose.yml --profile observability up -d prometheus
open http://127.0.0.1:9090            # 只监听回环，不会暴露到公网
```

抓取目标：`api:8000/metrics`（compose 网络内直连，不经 nginx —— nginx 只反代
`/api` 与 `/ws`）。想限制抓取来源就设 `METRICS_TOKEN`，并在 `prometheus.yml`
里启用 `authorization.credentials_file`。

## 暴露了什么

| 指标 | 类型 | 标签 | 用途 |
|---|---|---|---|
| `aw_http_requests_total` | counter | method / route / status | 请求量、错误率 |
| `aw_http_request_duration_seconds` | histogram | method / route | P95、慢接口定位 |
| `aw_ws_connections_active` | gauge | — | 实时连接数 |
| `aw_llm_calls_total` | counter | model / outcome | 上游失败率 |
| `aw_llm_call_duration_seconds` | histogram | model | 模型延迟 |
| `aw_llm_cost_cents_total` | counter | model | 成本（微分精度累计，渲染时是分） |
| `aw_run_terminal_total` | counter | status | 任务成败趋势 |
| `aw_sandbox_executions_total` | counter | status | 沙箱非成功速率 |
| `aw_sandbox_execution_duration_seconds` | histogram | language | 沙箱耗时 |
| `aw_celery_queue_depth` | gauge | — | 队列积压（抓取时读 broker） |

`route` 标签用的是**路由模板**（`/api/v1/runs/{run_id}`），不是真实路径：
否则每个 run_id 一条时间序列，Prometheus 会被直接撑爆。

## 告警

`alerts.yml` 里 8 条规则，阈值都对着实测基线取（见文件头注释）。每条都带
`severity` 与 `for`，可直接接 Alertmanager：

```yaml
route:
  receiver: default
  routes:
    - matchers: ['severity="critical"']
      receiver: oncall
receivers:
  - name: default
  - name: oncall
    webhook_configs:
      - url: https://your-webhook.example/alert
```

本项目刻意**不把 Alertmanager 打进 compose**：告警投递目标（webhook / 邮件 /
企业微信）属于部署环境的事，写死一个假接收端只会让人以为「告警配好了」。

## 契约门禁

`deploy/verify/test_alert_rules.py` 会比对「告警表达式里引用的指标」与
「`app/core/metrics.py` 实际注册的指标」。这条门禁的意义在于：
**告警里写错指标名，Prometheus 不报错，规则只是永远不触发** —— 看起来配了告警，
出事时一条都不响。`promtool check rules` 只校验语法，查不出这类问题。

另外它还强制：每条规则必须有 `severity`、`for`、`summary`；每个暴露的指标要么
出现在某条规则里，要么在 `intentionally_unwatched` 里显式登记。

## 尚未验证的部分（如实记录）

- **告警阈值本身没有在真实负载下验证过**。规则能通过 `promtool check rules`、
  指标名有契约测试保证一致，但「5xx > 5% 持续 5 分钟」这类阈值是否合适，
  需要真实流量才能调。这也是 003 `release-status.md` 里「告警阈值验证」至今
  未闭环的原因 —— 它依赖真实部署，不是代码问题。
- 没有 Grafana 面板：指标语义已固定，面板可以后补，不影响告警。
