# syntax=docker/dockerfile:1
# 网络规则 init 镜像（002-sandboxed-tools，research R-02）。
#
# 用途：被 Worker 以**短命容器**方式调用，只做三件事：
#   1. 给每次执行的独立 bridge 网络插入 FORWARD 链黑名单 DROP 规则；
#   2. 把该网络的 DNS 出口钉死到 dns-proxy（其余 53 端口一律 DROP）；
#   3. 执行结束时用 `conntrack -L` 快照该容器 IP 的真实连接目标。
#
# 为什么要独立镜像 + `--net=host --cap-add=NET_ADMIN`：
#   Worker 自己**不能**拿 NET_ADMIN。一旦 Worker 进 host 网络命名空间，
#   它就同时失去连 postgres/redis 的 compose 网络（见 research R-02 的
#   Alternatives）。把特权操作隔离到这个短命容器里，Worker 的权限面不变。
#
# ⚠️ 本镜像**不接受任何用户输入**：所有命令由 Worker 用固定模板拼装，
#    变量只有网络名、容器 IP、黑名单 CIDR 列表（均来自服务端配置/DB）。
#
# 重建：
#   docker build -t aw-sbx-net:1 deploy/sbx-net

FROM alpine:3.20

# iptables：拦截；conntrack-tools：连接快照；iproute2：读接口与地址
RUN apk add --no-cache \
        iptables \
        conntrack-tools \
        iproute2 \
        iputils

# 故意不设 ENTRYPOINT：Worker 显式指定命令（sh -c '<固定模板>'）。
# 若被误用为常驻容器，立即退出，不留下任何网络规则。
ENTRYPOINT []
CMD ["sh", "-c", "echo 'sbx-net image: short-lived only, launched by worker' >&2; exit 2"]
