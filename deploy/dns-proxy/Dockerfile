# syntax=docker/dockerfile:1
# DNS 代理镜像（002-sandboxed-tools，research R-02 第 2 层）：记录查询 + 黑名单。
#
# 只被 Worker 把沙箱容器接入，沙箱的 /etc/resolv.conf 由 Docker 指向本服务。
# 本镜像**不暴露端口到宿主**，只在 compose 内部网络可达。

FROM alpine:3.20

RUN apk add --no-cache dnsmasq \
    && mkdir -p /etc/dnsmasq.d /etc/aw

COPY dnsmasq.conf /etc/dnsmasq.conf
# 上游解析器列表在启动时从 EGRESS_DNS_UPSTREAMS 渲染，见 render-upstreams.sh
COPY render-upstreams.sh /etc/aw/render-upstreams.sh
COPY entrypoint.sh /etc/aw/entrypoint.sh
RUN chmod 0755 /etc/aw/render-upstreams.sh /etc/aw/entrypoint.sh

# 黑名单由 compose 从宿主机只读挂载（deploy/dns-proxy/blacklist.conf）。
# 这里预建一个空文件，使镜像单独运行时也能启动（不被挂载时 conf-dir 仍可用）。
RUN : > /etc/dnsmasq.d/blacklist.conf

# entrypoint：渲染上游列表 → exec dnsmasq -k（前台运行，容器需要 PID 1 在前台）
ENTRYPOINT ["/etc/aw/entrypoint.sh"]
