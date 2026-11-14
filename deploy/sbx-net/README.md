# 网络规则 init 镜像 `aw-sbx-net:1`

被 Worker 以短命容器方式调用，执行规则安装/清理命令；另有同镜像的 conntrack event watcher 覆盖用户进程生命周期。安装失败会阻止沙箱容器创建或启动。

## 为什么需要它

Worker 自己**不能**拿 `NET_ADMIN`。一旦 Worker 进 host 网络命名空间去改 iptables，它就同时失去连 `postgres`/`redis` 的 compose 网络——这是实测会直接打断执行链路的。

所以把特权操作隔离到一个短命容器里：

```
Worker (无特权, compose 网络)
   │  Docker SDK argv command + --network host + --cap-add NET_ADMIN
   ▼
init 容器 (host netns, 有 NET_ADMIN, 生命周期 = 一条命令)
   │  iptables -I FORWARD ...
   ▼
沙箱容器 (独立 bridge 网络 aw-sbx-{exec_id}, 无特权)
```

权限面：Worker 不变（挂 socket 的风险已在 research R-01 登记，归 P3 安全复核）；特权只存在于一个跑固定命令的短命容器里，且**不接受任何用户输入**。

## 三类调用（模板由 Worker 固定，变量只有网络名/IP/CIDR）

### 1. 执行前：装规则

```sh
# 黑名单 DROP（内网段 + 云元数据 + 回环）
iptables -I FORWARD -i aw-sbx-<exec_id> -d <cidr> -j DROP
# DNS 只许到 dns-proxy，其余 53 一律 DROP（改 resolv.conf 也绕不过）
iptables -I FORWARD -i aw-sbx-<exec_id> -p udp --dport 53 -d <dns_proxy_ip> -j ACCEPT
iptables -I FORWARD -i aw-sbx-<exec_id> -p tcp --dport 53 -d <dns_proxy_ip> -j ACCEPT
iptables -A FORWARD -i aw-sbx-<exec_id> -p udp --dport 53 -j DROP
iptables -A FORWARD -i aw-sbx-<exec_id> -p tcp --dport 53 -j DROP
```

规则按执行 UUID 生成私有链名，并按 Docker bridge interface 匹配。FORWARD 同时安装 IPv4/IPv6 policy；INPUT 对该 bridge 一律 DROP，避免容器直接访问宿主机服务。清理只删除当前执行的链和 jump，不清空全局 host 规则。命令通过 argv 传递，无 shell 拼接。

### 2. 限制模式（`egress_mode='restricted'`）额外装一组

```sh
# 只放行该 Agent 的 allow 项，其余全部 DROP
iptables -A FORWARD -i aw-sbx-<exec_id> -d <allowed_cidr_or_ip> -p tcp --dport <port> -j ACCEPT
iptables -A FORWARD -i aw-sbx-<exec_id> -j DROP
```

这组规则**追加在放行之后、作为该网络的最后一条**，所以不需要动 dns-proxy 的共享配置（见 `deploy/dns-proxy/README.md` 的「为什么黑名单是静态的」）。

### 3. 执行后：快照 + 清规则

```sh
# 快照该容器 IP 的全部连接（真实目标 IP:port + 字节数）
conntrack -L -s <sandbox_ip>
# 按网络名删掉本执行的所有规则
iptables -D FORWARD -i aw-sbx-<exec_id> ...   # 逐条
```

Worker 拿 `conntrack -L` 的输出 + dns-proxy 的日志做「IP → 域名」反查，得到 `egress_accesses` 记录。

## 重建与自检

```bash
docker build -t aw-sbx-net:1 deploy/sbx-net

# 工具齐备
docker run --rm --entrypoint sh aw-sbx-net:1 -c 'iptables --version; conntrack --version; ip -V'

# 误用即退出（不常驻）
docker run --rm aw-sbx-net:1; echo "exit=$?"   # 期望 exit=2
```

## 已知限制

- **只在 Linux 宿主有效**。Docker Desktop（macOS/Windows）的 iptables 语义不同，`conntrack` 也看不到容器流量。本地开发跑不通这部分，必须在 Linux 云主机验证（plan.md 的 Target Platform 已注明）。
- **`conntrack -L` 是快照，不是流水**。长连接跨执行存活时可能漏记；`TIME_WAIT` 条目仍可读，实测覆盖足够（research R-02）。
- **看不到 URL 路径与请求体**（不做 TLS 解密）。CDN 共享 IP 的域名反查可能不准，此时记 `ip-direct`。
- iptables 规则与 Docker 自身的 `DOCKER` 链共存：本镜像只往 `FORWARD` 插带 `-i aw-sbx-*` 匹配的规则，不影响其他容器。
