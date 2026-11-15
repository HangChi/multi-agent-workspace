# 受控 DNS 代理 `aw-dns-proxy:1`

沙箱的域名解析出口。**不是通用 DNS 服务**，只在 compose 内部网络可达，不映射宿主端口。

## 它做什么

| 职责 | 实现 | 作用 |
|---|---|---|
| 记录查询 | `log-queries=extra` → stdout | 日志形如 `query[A] example.com from 172.30.0.3`，Worker 按「客户端 IP + 时间窗」把 conntrack 记到的目标 IP 反查成域名 |
| 全局黑名单 | `/etc/dnsmasq.d/blacklist.conf` 里的 `address=/<domain>/` | 黑名单域名直接 NXDOMAIN，比等 iptables 拦 TCP 更早给出可读失败 |
| 不解析内网 | `no-resolv` + 显式公网上游 | 不用 Docker 内嵌 DNS（`127.0.0.11` 能解析 `postgres`/`redis` 等服务名），避免把内网拓扑暴露给用户脚本 |

**关键点：`log-queries=extra` 不能写成 `log-queries`。** 普通模式只记 `forwarded` / `reply`，**不带客户端 IP**，Worker 就无法把连接归到具体某次执行。这是实测确认的（普通模式下日志里没有任何 `query ... from <IP>` 行）。

## 它不做什么（重要）

**这层不是强制边界。** 强制边界是 iptables：

- 沙箱可以直接连 IP（不查 DNS），DNS 代理看不到；
- 沙箱可以改 `/etc/resolv.conf` 指向外部 DNS —— 但 iptables 只放行到本代理的 53 端口，所以改了也没用。

因此 DNS 层配错不会导致越权，只会让审计日志少了域名（目标 IP 仍由 conntrack 记到，域名记为 `ip-direct`）。

## 为什么黑名单是静态的

**本服务全局共享，配置只在部署时定，运行期不按执行改写。** 原因：并发执行改写同一份 `blacklist.conf` 会互相覆盖（后写的赢），还要处理 SIGHUP 重载竞态。

所以：

- **管理员维护的全局黑名单** → 落在这里，改完 `docker compose restart dns-proxy`；
- **Agent 级「限制模式」的允许域名** → 不在这里，落在每次执行独立的 iptables 规则里（每个执行一个独立网络 + 一组自己的规则，天然隔离）。

## 多网络接入与默认路由

DNS 代理会被 Worker 动态接入执行网络。Docker 可能在接入时重新选择默认网关；如果公网上游查询改从执行网桥发出，就会被该执行的端口 53 出口规则拒绝。正式与验收 Compose 均为代理的主网络设置 `gw_priority: 100`，执行网络保持默认优先级 0，不依赖网络名字或接入顺序。不得通过放行执行网络到任意 DNS、宿主 DNS 或 Docker 内置 DNS 来规避此问题。

部署要求 Docker Engine 与 Compose 支持 `gw_priority`（Engine 28+）；升级前先运行 `docker compose version` 并用 `docker compose config` 验证配置。调整此设置需要重建代理容器，而不只是重启旧容器。2026-11-16 的自有临时双网络探针复现了无优先级时的网关切换，并验证设置后主路由保持不变；这不是原生 Linux HTTP 验收通过的替代证据。

## 重建与自检

```bash
docker build -t aw-dns-proxy:1 deploy/dns-proxy

# 起一个临时网络验证
docker network create aw-test-dns
docker run -d --rm --name aw-test-dns-proxy --network aw-test-dns aw-dns-proxy:1
PROXY_IP=$(docker inspect aw-test-dns-proxy --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')

# 1) 公网域名可解析，且日志里有带源 IP 的 query 行
docker run --rm --network aw-test-dns --dns "$PROXY_IP" --entrypoint sh aw-sbx-net:1 \
  -c 'nslookup example.com' | tail -3
docker logs aw-test-dns-proxy 2>&1 | grep 'query\[' | tail -3

# 2) 内网服务名不解析
docker run --rm --network aw-test-dns --dns "$PROXY_IP" --entrypoint sh aw-sbx-net:1 \
  -c 'nslookup postgres' | tail -2   # 期望 No answer

# 3) 黑名单返回 NXDOMAIN
printf 'address=/evil.example.com/\n' > /tmp/bl.conf
docker rm -f aw-test-dns-proxy
docker run -d --rm --name aw-test-dns-proxy --network aw-test-dns \
  -v /tmp/bl.conf:/etc/dnsmasq.d/blacklist.conf:ro aw-dns-proxy:1
docker run --rm --network aw-test-dns --dns "$PROXY_IP" --entrypoint sh aw-sbx-net:1 \
  -c 'nslookup evil.example.com' | tail -2   # 期望 NXDOMAIN

# 清理
docker rm -f aw-test-dns-proxy; docker network rm aw-test-dns; rm -f /tmp/bl.conf
```

## 已知限制

- **缓存会合并重复查询**：同一次执行内重复查同一域名只记一条日志。用于「IP → 域名」反查足够，但**不能**用日志统计访问次数（次数看 conntrack 的连接条目）。
- **只记域名，不记 URL 路径与请求体**（不做 TLS 解密，见 research R-02 的 Known gaps）。
- 上游 DNS 写死为 `223.5.5.5` / `119.29.29.29`，内网环境需按实际情况改 `dnsmasq.conf`。
