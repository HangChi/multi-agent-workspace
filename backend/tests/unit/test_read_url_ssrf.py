"""read_url 的 SSRF 防护单测（015 覆盖率补齐）。

read_url 是 LLM 可以传任意 URL 的工具，等于一个 SSRF 入口。这里的每条规则
漏一条就是一个洞：

- 只允许 http/https（`javascript:alert(1)` 没有 `://`，不能被"补 https"绕过）
- 解析结果落在内网/保留地址一律拒绝（含 IPv6 与 IPv4-mapped IPv6）
- 解析不出 IP 的字符串按不安全处理（默认拒绝）
- 返回的 IP 列表**就是随后要连的地址**（调用方不能再解析，否则有 DNS rebinding 窗口）
- IPv4 优先排序（双栈环境里没有 IPv6 出口时，先试 IPv6 会白等超时）
"""

import ipaddress

import pytest

from app.executors.read_url import (
    FetchError,
    _connect_url,
    _is_blocked_ip,
    _normalize_url,
    resolve_public_hosts,
)


@pytest.mark.parametrize(
    "raw",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.1",
        "169.254.169.254",  # 云元数据地址
        "0.0.0.0",
        "::1",
        "::ffff:127.0.0.1",  # IPv4-mapped IPv6 也要识别
        "not-an-ip",
    ],
)
def test_blocked_addresses(raw):
    assert _is_blocked_ip(raw) is True


@pytest.mark.parametrize("raw", ["8.8.8.8", "1.1.1.1", "2001:4860:4860::8888"])
def test_public_addresses_allowed(raw):
    assert _is_blocked_ip(raw) is False


async def test_literal_public_ip_returns_itself():
    assert await resolve_public_hosts("8.8.8.8") == ["8.8.8.8"]


async def test_literal_private_ip_rejected():
    with pytest.raises(FetchError):
        await resolve_public_hosts("192.168.0.10")


async def test_empty_host_rejected():
    with pytest.raises(FetchError):
        await resolve_public_hosts("")


async def test_dns_failure_becomes_fetch_error(monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("no such host")

    monkeypatch.setattr("socket.getaddrinfo", _boom)

    with pytest.raises(FetchError):
        await resolve_public_hosts("nonexistent.invalid")


async def test_dns_result_deduped_and_ipv4_first(monkeypatch):
    """同一地址出现两次要去重；IPv6 排在后面（没有 v6 出口时不先白等）。"""

    def _fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (10, 1, 6, "", ("2001:4860:4860::8888", 0, 0, 0)),
            (10, 1, 6, "", ("8.8.8.8", 0)),
            (10, 1, 6, "", ("8.8.8.8", 0)),
        ]

    monkeypatch.setattr("socket.getaddrinfo", _fake_getaddrinfo)

    resolved = await resolve_public_hosts("example.test")
    # 去重后剩两个地址，且 IPv4 一定排在前面（_MAX_CONNECT_ATTEMPTS=2，只会连前两个）
    assert resolved == ["8.8.8.8", "2001:4860:4860::8888"]


async def test_resolution_with_private_address_rejected(monkeypatch):
    """域名解析到内网（DNS rebinding 的常见形态）必须整体拒绝。"""

    def _fake_getaddrinfo(host, port, *args, **kwargs):
        return [(10, 1, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr("socket.getaddrinfo", _fake_getaddrinfo)

    with pytest.raises(FetchError):
        await resolve_public_hosts("rebind.test")


def test_normalize_adds_https():
    assert _normalize_url("example.com/a?b=1") == "https://example.com/a?b=1"
    assert _normalize_url("  http://example.com  ") == "http://example.com"


def test_normalize_rejects_non_http_scheme():
    with pytest.raises(FetchError):
        _normalize_url("ftp://example.com/file")
    with pytest.raises(FetchError):
        _normalize_url("file:///etc/passwd")


def test_normalize_rejects_scheme_without_slashes():
    """`javascript:alert(1)` 没有 `://`，若直接补 https 就绕过了 scheme 检查。"""
    with pytest.raises(FetchError):
        _normalize_url("javascript:alert(1)")


def test_normalize_keeps_explicit_port():
    assert _normalize_url("example.com:8080/x") == "https://example.com:8080/x"


def test_normalize_rejects_empty():
    with pytest.raises(FetchError):
        _normalize_url("   ")


def test_connect_url_swaps_host_keeps_path_and_port():
    assert (
        _connect_url("https://example.com:8443/path?q=1", "203.0.113.9")
        == "https://203.0.113.9:8443/path?q=1"
    )
    # IPv6 要加方括号，否则 URL 解析会把地址里的冒号当端口分隔符
    assert (
        _connect_url("https://example.com/x", "2001:db8::1")
        == "https://[2001:db8::1]/x"
    )


def test_blocked_nets_cover_documented_ranges():
    """内置网段至少覆盖 RFC1918 + 回环 + link-local。"""
    nets = [str(net) for net in _blocked_nets()]
    for expected in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"):
        assert any(net == expected for net in nets)

    # 顺带确认这些网段确实覆盖到地址（不是只写了个字符串）
    assert ipaddress.ip_address("10.255.0.1") in ipaddress.ip_network("10.0.0.0/8")


def _blocked_nets():
    from app.executors import read_url

    return read_url._BLOCKED_NETS
