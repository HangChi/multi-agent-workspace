"""出网目标守卫（017 起从 read_url 抽出，018 复用）。

两处共用同一套判定，避免「LLM 工具挡了内网，但用户配的 webhook 没挡」这种
最危险的分叉：
- `app/executors/read_url.py`（LLM 可控 URL，威胁等级最高）；
- `app/services/notification_channel_service.py`（用户配置的 webhook URL）。

判定分两步：
1. `normalize_http_url`：只接受 http/https，拒绝 `javascript:` 之类没有 `://` 的写法；
2. `resolve_public_hosts`：解析后**任一** IP 落在内网/保留段即拒绝。

为什么不只用字符串匹配：`127.0.0.1` / `0.0.0.0` / `[::1]` / `10.x` / `192.168.x` /
`169.254.169.254`（云元数据）/ 「外网域名解析到 127.0.0.1」——字符串写法挡不住最后一种。
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

# 一次解析最多返回几个地址（域名可能同时有 A/AAAA 记录）。
MAX_CONNECT_ATTEMPTS = 2

BLOCKED_NETS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",  # CGNAT
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local / 云元数据
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "224.0.0.0/4",  # 组播
        "240.0.0.0/4",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


class NetGuardError(RuntimeError):
    """目标地址不允许（内网/保留段）或无法解析。"""


def is_blocked_ip(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return True  # 解析不出 IP 一律视为不安全
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in BLOCKED_NETS)


# 云元数据服务：能取到机器临时凭证，任何「允许内网」的开关都不该放开它。
METADATA_NETS = tuple(
    ipaddress.ip_network(net) for net in ("169.254.0.0/16", "fd00:ec2::254/128")
)


def is_metadata_address(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in METADATA_NETS)


async def resolve_hosts(host: str) -> list[str]:
    """纯解析（不做公网判定），供「部署级允许私网」的调用方使用。"""
    if not host:
        raise NetGuardError("URL 缺少主机名")

    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        pass

    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except OSError as exc:
        raise NetGuardError(f"域名解析失败: {host}") from exc
    seen: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in seen:
            seen.append(address)
    if not seen:
        raise NetGuardError(f"域名解析失败: {host}")
    return seen


async def resolve_public_hosts(host: str) -> list[str]:
    """解析主机并按公网策略过滤，返回**可直接连接**的 IP 列表。

    返回的就是随后要连的地址：调用方不能再解析一次，否则校验与连接之间会出现
    DNS rebinding 窗口（第一次返回公网 IP 通过校验，第二次返回 127.0.0.1）。
    """
    resolved = await resolve_hosts(host)
    for raw_ip in resolved:
        if is_blocked_ip(raw_ip):
            raise NetGuardError(f"目标地址不允许访问（内网/保留地址）: {host}")
    # IPv4 优先：双栈环境里先试 A 记录，避免没有 IPv6 出口时白等一轮超时。
    return sorted(resolved, key=lambda value: ":" in value)[:MAX_CONNECT_ATTEMPTS]


def normalize_http_url(raw: str) -> str:
    """校验 scheme 并补全协议；失败抛 NetGuardError。

    注意不能只看 "://"：`javascript:alert(1)` 没有 "://"，若直接补 https
    就绕过了 scheme 检查。
    """
    url = (raw or "").strip()
    if not url:
        raise NetGuardError("URL 为空")

    if "://" in url:
        scheme = url.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            raise NetGuardError(f"仅支持 http/https，收到: {scheme or '空'}")
    else:
        head = url.split("/", 1)[0]
        if ":" in head:
            _, _, port = head.partition(":")
            if not port.isdigit():
                raise NetGuardError(f"仅支持 http/https，收到: {head.split(':', 1)[0]}")
        url = f"https://{url}"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise NetGuardError(f"仅支持 http/https，收到: {parsed.scheme or '空'}")
    if not parsed.hostname:
        raise NetGuardError("URL 缺少主机名")
    return url
