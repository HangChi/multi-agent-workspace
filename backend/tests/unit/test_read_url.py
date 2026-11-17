"""read_url 工具单元测试：正文提取 + SSRF 防护。

LLM 可控的 URL 是典型 SSRF 入口：不校验就能读云元数据（169.254.169.254）
或内网服务。这里锁定「仅公网 http/https + 重定向逐跳校验」，以及两条更细的
保证：校验与连接用同一份解析结果（DNS rebinding 无效），响应体超限即停止下载。
"""

import httpx
import pytest

from app.executors.read_url import (
    FetchError,
    ReadUrlExecutor,
    _normalize_url,
    html_to_text,
    resolve_public_hosts,
)

PUBLIC_IP = "93.184.216.34"


async def _public(host: str) -> list[str]:
    """测试用解析：只拦明显的内网字面量（不依赖 DNS，沙箱无外网）。"""
    if host in {"127.0.0.1", "localhost", "169.254.169.254", "10.0.0.5"}:
        raise FetchError(f"目标地址不允许访问（内网/保留地址）: {host}")
    return [PUBLIC_IP]


class TestNormalizeUrl:
    def test_adds_https_scheme(self):
        assert _normalize_url("example.com/post") == "https://example.com/post"

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://x/y", "javascript:alert(1)"])
    def test_rejects_non_http_schemes(self, url: str):
        with pytest.raises(FetchError, match="仅支持 http/https"):
            _normalize_url(url)

    def test_rejects_empty(self):
        with pytest.raises(FetchError, match="为空"):
            _normalize_url("   ")


class TestSSRFGuard:
    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1",
            "10.0.0.5",
            "172.16.3.4",
            "192.168.1.1",
            "169.254.169.254",  # 云元数据服务
            "0.0.0.0",
            "::1",
            "localhost",  # 解析到 127.0.0.1
            "fd00::1",
        ],
    )
    async def test_blocks_internal_targets(self, host: str):
        with pytest.raises(FetchError, match="内网|不允许"):
            await resolve_public_hosts(host)

    async def test_blocks_empty_host(self):
        with pytest.raises(FetchError):
            await resolve_public_hosts("")

    async def test_allows_public_ip(self):
        assert await resolve_public_hosts(PUBLIC_IP) == [PUBLIC_IP]

    async def test_ipv4_is_preferred_over_ipv6(self, monkeypatch):
        """双栈域名先试 A 记录：没有 IPv6 出口时不必白等一轮超时。"""
        import socket

        infos = [
            (socket.AF_INET6, 1, 6, "", ("2606:2800:220:1::1", 0, 0, 0)),
            (socket.AF_INET, 1, 6, "", (PUBLIC_IP, 0)),
        ]
        monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: infos)
        assert await resolve_public_hosts("example.com") == [PUBLIC_IP, "2606:2800:220:1::1"]

    async def test_any_internal_answer_rejects_the_whole_name(self, monkeypatch):
        """解析结果里只要有一个内网地址就整体拒绝（多 A 记录投毒）。"""
        import socket

        infos = [
            (socket.AF_INET, 1, 6, "", (PUBLIC_IP, 0)),
            (socket.AF_INET, 1, 6, "", ("127.0.0.1", 0)),
        ]
        monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: infos)
        with pytest.raises(FetchError, match="内网|不允许"):
            await resolve_public_hosts("example.com")


class TestHtmlToText:
    def test_strips_tags_and_scripts(self):
        html = """
        <html><head><script>var a=1;</script><style>.x{}</style></head>
        <body><h1>标题</h1><p>第一段</p><p>第二段</p></body></html>
        """
        text = html_to_text(html)
        assert "标题" in text
        assert "第一段" in text
        assert "var a=1" not in text
        assert "<h1>" not in text

    def test_unescapes_entities(self):
        assert "a & b" in html_to_text("<p>a &amp; b</p>")


def _executor(handler, *, resolver=_public) -> ReadUrlExecutor:
    """注入 MockTransport 的 executor（不联网）。"""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return ReadUrlExecutor(client=client, resolver=resolver)


class TestExecute:
    async def test_fetches_html_as_text(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html><body><p>正文内容</p></body></html>",
            )

        result = await _executor(handler).execute({"url": "https://example.com/a"})
        assert result["status"] == 200
        assert "正文内容" in result["text"]
        assert result["truncated"] is False
        assert result["body_truncated"] is False

    async def test_plain_text_passthrough(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"content-type": "text/plain"}, text="raw text"
            )

        result = await _executor(handler).execute({"url": "https://example.com/a.txt"})
        assert result["text"] == "raw text"

    async def test_http_error_reported(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        result = await _executor(handler).execute({"url": "https://example.com/missing"})
        assert result["status"] == 404
        assert "error" in result

    async def test_connects_to_the_validated_ip_not_a_second_lookup(self):
        """校验通过后直接连那个 IP，Host 与 SNI 仍是原域名。

        这是 DNS rebinding 的正面防线：如果实现里「校验解析一次、连接再解析
        一次」，攻击者只要第二次返回 127.0.0.1 就能读到内网。
        """
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, headers={"content-type": "text/plain"}, text="ok")

        result = await _executor(handler).execute({"url": "https://example.com/a?x=1"})
        assert result["status"] == 200
        assert len(seen) == 1
        request = seen[0]
        assert request.url.host == PUBLIC_IP  # 连的是校验过的地址
        assert request.url.path == "/a" and request.url.query == b"x=1"
        assert request.headers["host"] == "example.com"  # 虚拟主机头仍是域名
        assert request.extensions.get("sni_hostname") == "example.com"

    async def test_redirect_to_internal_host_blocked(self):
        """302 跳内网必须在跟随前拦截（防止绕过首跳校验）。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/redir":
                return httpx.Response(302, headers={"location": "http://127.0.0.1:8080/admin"})
            raise AssertionError("不应真的请求内网地址")

        result = await _executor(handler).execute({"url": "https://example.com/redir"})
        assert "error" in result
        assert "内网" in result["error"] or "不允许" in result["error"]

    async def test_redirect_keeps_host_and_follows_on_the_public_ip(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/old":
                return httpx.Response(301, headers={"location": "/new"})
            assert request.url.host == PUBLIC_IP
            assert request.headers["host"] == "example.com"
            return httpx.Response(200, headers={"content-type": "text/plain"}, text="moved")

        result = await _executor(handler).execute({"url": "https://example.com/old"})
        assert result["text"] == "moved"
        assert result["url"] == "https://example.com/new"

    async def test_body_over_the_cap_stops_reading(self):
        """超过 2MB 就停止下载：不把整个 body 读进内存。"""
        from app.executors import read_url as module

        sent_chunk = b"x" * (256 * 1024)
        total_chunks = 64  # 16MB，若全读会占满内存
        pulled = 0

        class _Chunks(httpx.AsyncByteStream):
            async def __aiter__(self):
                nonlocal pulled
                for _ in range(total_chunks):
                    pulled += 1
                    yield sent_chunk

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"content-type": "text/plain"}, stream=_Chunks()
            )

        result = await _executor(handler).execute({"url": "https://example.com/big.txt"})
        assert result["body_truncated"] is True
        assert result["truncated"] is True
        # 只读到上限就停：远少于 64 个 chunk
        assert pulled <= module._MAX_BYTES // len(sent_chunk) + 1
        assert pulled < total_chunks

    async def test_connect_failure_moves_to_the_next_resolved_address(self):
        attempts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request.url.host)
            if request.url.host == "93.184.216.1":
                raise httpx.ConnectError("unreachable", request=request)
            return httpx.Response(200, headers={"content-type": "text/plain"}, text="second")

        async def resolver(host: str) -> list[str]:
            return ["93.184.216.1", PUBLIC_IP]

        result = await _executor(handler, resolver=resolver).execute(
            {"url": "https://example.com/a"}
        )
        assert result["text"] == "second"
        assert attempts == ["93.184.216.1", PUBLIC_IP]

    async def test_all_addresses_unreachable_is_an_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("unreachable", request=request)

        result = await _executor(handler).execute({"url": "https://example.com/a"})
        assert "error" in result
        assert "抓取失败" in result["error"]

    async def test_internal_url_never_requested_with_real_guard(self):
        """使用默认策略（真实 resolve_public_hosts）时内网地址也不发请求。"""

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("不应发起请求")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        result = await ReadUrlExecutor(client=client).execute(
            {"url": "http://127.0.0.1:8000/admin"}
        )
        assert "error" in result

    async def test_internal_url_never_requested(self):
        """直接给内网地址：请求根本不发出。"""

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("不应发起请求")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        result = await ReadUrlExecutor(client=client).execute(
            {"url": "http://169.254.169.254/latest/meta-data/"}
        )
        assert "error" in result
        assert "不允许" in result["error"] or "内网" in result["error"]

    async def test_missing_location_header_is_reported(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302)

        result = await _executor(handler).execute({"url": "https://example.com/a"})
        assert "重定向缺少 Location" in result["error"]
