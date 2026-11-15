# 网络规则与采集镜像 `aw-sbx-net:1`

Worker 通过固定 argv 启动短命 helper 安装/清理规则，并在用户进程运行期间启动执行专属的包头 watcher。安装或 watcher 就绪失败都阻止用户代码启动；不允许失败后无策略运行。

## 权限与生命周期

- Worker 保持在 compose 网络，不授予 `NET_ADMIN`；Docker socket 的宿主 root 等价风险仍存在，见 research R-01。
- 规则 helper 使用 `network_mode=host` 和 `NET_ADMIN`。watcher 另需 `NET_RAW`，但这些能力**不授予用户沙箱**。
- 每次执行创建独立 bridge；按执行 UUID 派生私有 FORWARD/INPUT 链。接口使用 Docker 实际的 `br-…` 名，不是逻辑 network 名。
- 清理只删除本次执行的 jump/链、watcher 与 DNS attachment，不 flush 全局规则，不 prune 其他资源。
- 用户代码只能经 `put_archive` 作为文件进入用户容器；helper 参数只来自服务端策略快照与 Docker 网络元数据。

## 启动顺序

1. 创建独立网络并接入受控 DNS proxy。
2. 编译并安装 IPv4/IPv6 策略：受控 DNS 例外、内置黑名单、全局 deny、restricted IP/CIDR allow、默认兜底。INPUT 拒绝访问宿主服务。DNS 的 established reply 也必须在私网 DROP 前放行，否则同 bridge 返回包会被拦。
3. 给尚未启动的用户容器配置固定 IPAM endpoint。Docker 在 start 前不会填充动态 `IPAddress`，不能依赖它启动采集。
4. 启动 `tcpdump --immediate-mode -l -n -tt -i <bridge> src host <sandbox_ipv4> and (tcp or udp)` watcher，等待 listening banner 后才启动用户进程。
5. 执行结束后停止 watcher，解析目标并与该窗口 DNS `reply` / `cached` 日志关联，形成访问记录；然后清理。

## 为什么不能只用 conntrack

2026-11-15 的真实 Docker 测试证明：在 FORWARD 被 DROP 的 SYN 不会形成已确认 conntrack 事件，原先 `conntrack -E` 会漏掉 deny。现在从 bridge 的防火墙前侧观察 TCP/UDP 尝试，只读取包头摘要，不输出十六进制 payload，不解密 TLS。`conntrack-tools` 仍保留供排障和既有解析测试使用，不再作为 deny 审计的唯一来源。

## 重建与验证

```bash
docker build -t aw-sbx-net:1 deploy/sbx-net
docker run --rm aw-sbx-net:1 tcpdump --version
docker run --rm --network host --cap-add NET_ADMIN aw-sbx-net:1 iptables -S FORWARD

# 独立验收 DNS 容器已启动时；不会连接应用数据库
EGRESS_DNS_PROXY=aw-002-verify-dns SBX_NETWORK_PREFIX=aw-002-probe \
  backend/.venv/bin/python deploy/verify/s5_egress_scenarios.py \
  --public-url https://pypi.org --output /tmp/aw-002-egress.json
```

2026-11-15 已在 Docker Desktop 的 **Linux VM**（Engine 29.9.3、linux/arm64、7.0.14-linuxkit）完成 IP 策略 7 项运行时检查，不再笼统认为 macOS 上无法验证。这不是原生 Linux 云主机全量验收；证据见 `specs/002-sandboxed-tools/docker-verification.md`。

## 尚未解决的限制

- restricted domain allow 与全局 domain deny 仍 fail-closed：没有实现安全 DNS 响应到内核集合的动态绑定。API 保存成功不代表域名策略可运行。
- 内核安装 IPv6 策略，但当前 watcher 只按沙箱 IPv4 过滤；ICMP、完整 IPv6 审计及高流量丢包覆盖率未验证，不声称 SC-009 的 100%。
- 记录按 protocol/IP/port 去重，不等于每连接流水；`bytes_out` 当前为 null，不编造字节统计。
- 共享 IP、CNAME 或无法唯一关联的域名记 `ip-direct`。DNS 例外流量标为 `dns-proxy`，不误判成私网 deny。
- 内置黑名单记录目前 `matched_rule_id=null`（内置项不是 DB 规则行）；自定义全局/Agent 规则有对应 UUID。
- 多次短命 helper 启动有开销；SC-001 必须计入策略准备/回收，而不能只看用户进程的 `duration_ms`。
