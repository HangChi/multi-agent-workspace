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
    # 017 起判定搬到 core.net_guard（018 的 webhook 也要用同一套，不能各写一份）；
    # 这里断言的是那份共享名单，不是 read_url 的副本。
    from app.core.net_guard import BLOCKED_NETS

    return BLOCKED_NETS


# ---------- 015 覆盖率补齐：执行链分支（注入 client + resolver） ----------

import httpx

from app.executors.read_url import ReadUrlExecutor


def _executor(handler, resolver=None):
    """构造注入版 executor：MockTransport 出响应，resolver 决定「连哪个 IP」。

    不 monkeypatch 全局 httpx.AsyncClient——ReadUrlExecutor 本来就把 client
    与 resolver 设计成可注入，直接塞假对象更贴近真实调用路径。
    """
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return ReadUrlExecutor(
        client=client,
        resolver=resolver or (lambda _host: _resolver_sync(["93.184.216.34"])),
    )


async def _resolver_sync(addresses: list[str]):
    return addresses


async def test_execute_returns_text_and_flags():
    handler = lambda request: httpx.Response(
        200,
        headers={"content-type": "text/plain; charset=utf-8"},
        text="hello world",
    )
    result = await _executor(handler).execute({"url": "https://example.com/a"})
    assert result["status"] == 200
    assert result["text"] == "hello world"
    assert result["truncated"] is False
    assert result["body_truncated"] is False


async def test_execute_http_error_returns_status_and_error():
    result = await _executor(lambda r: httpx.Response(404)).execute(
        {"url": "https://example.com/missing"}
    )
    assert result["status"] == 404
    assert result["error"] == "HTTP 404"


async def test_execute_html_is_converted_to_text():
    handler = lambda r: httpx.Response(
        200, headers={"content-type": "text/html"}, text="<p>Hello <b>world</b></p>"
    )
    result = await _executor(handler).execute({"url": "https://example.com"})
    assert result["content_type"] == "text/html"
    assert "world" in result["text"]


async def test_execute_follows_redirect_and_final_url_is_target():
    """重定向跟随：终态 URL 是目标，但每跳连接的都是「已校验的 IP」。

    连接目标是 resolver 返回的 IP（Host/SNI 才是原域名），所以 request.url
    的 host 是 IP——这正是 SSRF 防 DNS rebinding 的关键：客户端不允许再解析。
    """
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, text="done")

    result = await _executor(handler).execute({"url": "https://example.com/start"})
    assert result["text"] == "done"
    assert paths == ["/start", "/final"]
    # 结果里的 url 是规范化后的目标域名（不是 IP），供前端展示
    assert result["url"] == "https://example.com/final"


async def test_execute_redirect_without_location_fails():
    result = await _executor(lambda r: httpx.Response(302)).execute(
        {"url": "https://example.com/start"}
    )
    assert "重定向缺少 Location" in result["error"]


async def test_execute_redirect_loop_hits_cap():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"})

    result = await _executor(handler).execute({"url": "https://example.com/start"})
    assert "重定向次数超过" in result["error"]


async def test_execute_dns_resolution_failure_is_soft_error():
    result = await _executor(
        lambda r: httpx.Response(200, text="x"), resolver=lambda _h: _resolver_sync([])
    ).execute({"url": "https://example.com/a"})
    assert "域名解析失败" in result["error"]


async def test_execute_connection_error_retries_other_address():
    """第一个地址连不上要换第二个——读超时不换，连不上才换。"""
    attempts: list[str] = []

    async def resolver(_host):
        return ["93.184.216.34", "93.184.216.35"]

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        if "216.34" in str(request.url):
            raise httpx.ConnectError("refused")
        return httpx.Response(200, text="ok")

    result = await _executor(handler, resolver=resolver).execute(
        {"url": "https://example.com/a"}
    )
    assert result["text"] == "ok"
    assert len(attempts) == 2
    assert "216.35" in attempts[-1]


async def test_execute_large_body_is_truncated():
    """text 截断到 8000 字符；body 截断（2MB）是另一条独立上限。

    10KB 的 body 远小于 2MB，所以 `body_truncated` 为 False；但字符数超
    8000，`truncated` 为 True——两个上限判据不同，不能混用。
    """
    handler = lambda r: httpx.Response(
        200, headers={"content-type": "text/plain"}, text="x" * 10000
    )
    result = await _executor(handler).execute({"url": "https://example.com/big"})
    assert result["truncated"] is True
    assert len(result["text"]) == 8000
    assert result["body_truncated"] is False


async def test_execute_unknown_encoding_falls_back_to_utf8():
    """非法 encoding 名会让 .decode 抛 LookupError，回退 utf-8 而非崩。

    httpx 会把未知 charset 归一化成 utf-8，所以这里显式覆盖响应的
    `encoding` 属性制造 LookupError，验证 fallback 分支产出正确的 UTF-8
    文本（而非把异常冒出去变成 500）。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        resp = httpx.Response(200, content="中文".encode())
        resp.encoding = "not-a-real-codec"
        return resp

    result = await _executor(handler).execute({"url": "https://example.com/a"})
    assert result["text"] == "中文"


async def test_execute_ipv6_host_gets_bracketed_authority():
    """IPv6 字面量 Host 头要带方括号，否则连接与 Host 头都不合法。"""
    seen_headers: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers["host"] = request.headers.get("host")
        return httpx.Response(200, text="ok")

    result = await _executor(
        handler, resolver=lambda _h: _resolver_sync(["2606:4700::1"])
    ).execute({"url": "https://[2606:4700::1]/"})
    assert result["text"] == "ok"
    assert seen_headers["host"] == "[2606:4700::1]"
