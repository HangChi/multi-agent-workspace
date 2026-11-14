"""read_url 工具单元测试：正文提取 + SSRF 防护。

LLM 可控的 URL 是典型 SSRF 入口：不校验就能读云元数据（169.254.169.254）
或内网服务。这里锁定「仅公网 http/https + 重定向逐跳校验」。
"""

import httpx
import pytest

from app.executors.read_url import (
    FetchError,
    ReadUrlExecutor,
    _assert_public_host,
    _normalize_url,
    html_to_text,
)


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
        ],
    )
    async def test_blocks_internal_targets(self, host: str):
        with pytest.raises(FetchError, match="内网|不允许"):
            await _assert_public_host(host)

    async def test_blocks_empty_host(self):
        with pytest.raises(FetchError):
            await _assert_public_host("")

    async def test_allows_public_ip(self):
        await _assert_public_host("93.184.216.34")  # example.com 的公网 IP


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


async def _allow_public(host: str) -> None:
    """测试用宽松校验：只拦明显的内网字面量（不依赖 DNS，沙箱无外网）。"""
    if host in {"127.0.0.1", "localhost", "169.254.169.254", "10.0.0.5"}:
        raise FetchError(f"目标地址不允许访问（内网/保留地址）: {host}")


def _executor(handler, *, host_check=_allow_public) -> ReadUrlExecutor:
    """注入 MockTransport 的 executor（不联网）。"""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return ReadUrlExecutor(client=client, host_check=host_check)


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

    async def test_redirect_to_internal_host_blocked(self):
        """302 跳内网必须在跟随前拦截（防止绕过首跳校验）。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                return httpx.Response(302, headers={"location": "http://127.0.0.1:8080/admin"})
            raise AssertionError("不应真的请求内网地址")

        result = await _executor(handler).execute({"url": "https://example.com/redir"})
        assert "error" in result
        assert "内网" in result["error"] or "不允许" in result["error"]

    async def test_internal_url_never_requested_with_real_guard(self):
        """使用默认策略（真实 _assert_public_host）时内网地址也不发请求。"""

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

        result = await _executor(handler).execute(
            {"url": "http://169.254.169.254/latest/meta-data/"},
            # 默认策略（真实 _assert_public_host）也在此路径生效
        )
        assert "error" in result
        assert "不允许" in result["error"] or "内网" in result["error"]
