# 可观测性（Prometheus）

## 启动

```bash
# 指标随 API 一起暴露，Prometheus / Grafana 用 profile 单独拉起
docker compose -p aw -f deploy/docker-compose.yml --profile observability up -d prometheus grafana
open http://127.0.0.1:9090            # Prometheus，只监听回环
open http://127.0.0.1:3000            # Grafana，同样只监听回环
```

Grafana 首次登录用 `.env` 里的 `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`
（默认 `admin` / `admin`，**上线前必须改**）。

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

## Grafana 面板

`grafana/` 下是完整的 provisioning，容器起来就是可用状态（不需要手工点选数据源、
不需要导入 JSON）：

```
grafana/
  provisioning/datasources/prometheus.yml   # 数据源，uid 固定为 aw-prometheus
  provisioning/dashboards/dashboards.yml    # 看板加载规则
  dashboards/aw-overview.json               # 总览看板（10 个 panel）
```

**`allowUiUpdates=false` 是刻意的**：看板是仓库里的代码，界面上改了会在下次重启
被覆盖。要改面板请改 JSON 并提交——「告警/看板口径是什么时候、被谁改的」才是
需要留痕的东西。

总览看板覆盖：请求速率（按状态码）、5xx 比例（带 2% / 5% 阈值着色）、P95 延迟、
LLM 成本（元/小时，按模型）、LLM 调用速率（按 outcome）、Run 终态速率、
沙箱执行、Celery 队列积压、活跃 WS 连接、Prometheus 抓取状态。

> 改数据源 uid 时**必须同步改** `aw-overview.json` 里的 `datasource.uid`，
> 否则看板打开后每个 panel 都是「Datasource not found」。

## 管理页里的运行期计数器

除了 Prometheus，`/settings/admin` 页有一块「外部 MCP 运行指标」，读的是
`GET /external-tools/observability`。它和 Prometheus 的关系要分清：

| | Prometheus | 管理页计数器 |
|---|---|---|
| 口径 | 全局聚合（多 worker 合并） | **单个 API 进程本地**的 Counter |
| 保留 | 15 天（TSDB） | 进程生命周期，重启归零 |
| 用途 | 趋势、告警 | 现场排障：这个进程刚才到底遇到了什么 |

`external_observability` 的告警阈值（auth_error ≥5、policy_denied ≥10 等）
是**进程内即时判定**，用来快速回答「现在是不是在批量失败」，不替代 `alerts.yml`
里基于 rate 的持续劣化告警。

## 尚未验证的部分（如实记录）

- **告警阈值本身没有在真实负载下验证过**。规则能通过 `promtool check rules`、
  指标名有契约测试保证一致，但「5xx > 5% 持续 5 分钟」这类阈值是否合适，
  需要真实流量才能调。这也是 003 `release-status.md` 里「告警阈值验证」至今
  未闭环的原因 —— 它依赖真实部署，不是代码问题。
- Grafana 看板的 panel 表达式按 `metrics.py` 的指标名写，但**没有跑过数据回放**：
  它是「有真实流量时才看得出对不对」的东西，与告警阈值同属待真机验证项。
