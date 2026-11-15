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
3. 以 DNS proxy 的实际 IPv4/IPv6 地址各加一预留用户 endpoint；调用方将 `capture.source_ip` 与非空的 `capture.source_ipv6` 注入尚未启动容器的 `IPAMConfig.IPv4Address` / `IPv6Address`。Docker 在 start 前不会填充动态地址；IPv6 网络若没有可核验的预启动地址，拒绝执行而不是启动后才补采集。
4. 启动 `tcpdump --immediate-mode -l -nn -S -tt -i <bridge> (src host <sandbox_ipv4> or src host <sandbox_ipv6>) and (tcp or udp)` watcher（IPv4-only 网络省略 IPv6 分支），等待 listening banner 后才启动用户进程。`-S` 保留绝对 TCP 初始序列号以区分五元组复用。
5. 执行结束后停止 watcher，核验退出状态、零 kernel/interface drop 和输出包数完整性，再解析连接事实并与该窗口 DNS `reply` / `cached` 日志作非权威关联。记录保留实际源 IP/端口、首次观察时间；最后清理。watcher 限制为 64MiB/16pids、只读根目录和固定 4MiB/单文件 local 日志；截断、丢包、无统计或异常退出均报基础设施错误，不能当作无访问成功。

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

本轮执行专属 fixture 的 S5 检查为 **11/11**：上述 IPv4 策略、同目标多条允许/拒绝 TCP 连接，以及受控 `2001:db8::/32` IPv6 目标的允许/拒绝/全局 deny 优先级；每次执行资源残留为零。IPv6 INPUT 只为网关邻居发现增加 NS/NA 且 hop-limit=255 的窄例外，其他宿主服务仍 DROP。这个结果不包含动态域名策略，也不代表所有 IPv6/非 TCP 协议已验收。

## 尚未解决的限制

- **domain 运行时仍未完成**：exact/wildcard allow 与全局 domain deny 均保持启动前 fail-closed，API 未移除，也没有以安装时解析一次替代动态策略。现有共享 dnsmasq 日志没有可信的逐客户端 TTL/CNAME 响应证据；仅把日志转为 ipset 会有应答先于规则生效、陈旧授权与并发更新竞态。最小后续路径是执行专属可信 DNS helper：校验应答和完整 CNAME 链，按真实 TTL 维护按执行隔离集合，在应答交付前按 blacklist/global deny 优先序原子更新；更新/健康失败不交付可放行应答，IPv6 未覆盖时封闭。需独立实现并验证该响应中介，不能靠本文宣称功能已交付。范围是 DNS→目标地址策略，不是 TLS 身份认证或解密。
- watcher 已支持已预留的真实 IPv6 源地址；未知预启动 IPv6 地址拒绝执行。这里只验证 TCP/UDP 包头，ICMP、非首片/扩展头的完整语义、IPv6 link-local 多源地址以及高流量 100% 覆盖率未完成，不声称 SC-009 的 100%。捕获但无法解析的包会以审计不完整报错，不能静默省略。
- TCP 按源/目标 IP、源/目标端口、协议及 SYN 初始序列号/关闭状态记录，同目标多连接不再合并；SYN 重传仍只记一次。UDP 按五元组记录流，无法仅凭包头区分同五元组的 socket 关闭重开；conntrack 解析器可用 NEW/DESTROY/id 区分生命周期，但不是当前 deny 主采集路径。仅有快照、缺失 SYN 或完全相同五元组/ISN 的重用仍可能不可辨识，不将其声称为完整 socket 级流水。`bytes_out` 当前为 null，不编造字节统计。
- 共享 IP、CNAME 或无法唯一关联的域名记 `ip-direct`。DNS 例外流量标为 `dns-proxy`，不误判成私网 deny。
- 内置黑名单记录目前 `matched_rule_id=null`（内置项不是 DB 规则行）；自定义全局/Agent 规则有对应 UUID。
- 多次短命 helper 启动有开销；SC-001 必须计入策略准备/回收，而不能只看用户进程的 `duration_ms`。
