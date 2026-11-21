"""read_url 工具（真实实现）：抓取网页正文。

此前是 NoopExecutor 占位——LLM 调 read_url 拿到「占位」提示，会把"读过网页"
写进结论，造成看似有依据实则编造的输出。

安全约束（LLM 可控的 URL 是典型的 SSRF 入口）：
- 仅 http/https
- 解析后的 IP 必须在公网（拒绝 127/10/172.16/192.168/169.254 元数据地址等）
- **校验与连接用同一份解析结果**：校验通过后直接连那个 IP（Host 头与 SNI 仍是
  原始域名），否则「校验时解析一次、连接时再解析一次」会被 DNS rebinding 绕过
- 重定向手动跟随，每跳重新解析并重新校验（防止 302 跳到内网）
- 响应体边读边判上限 2MB（不把整个 body 读进内存）、总超时 15s
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from app.core.net_guard import (
    NetGuardError,
    is_blocked_ip,
    normalize_http_url,
)
from app.core.net_guard import (
    resolve_public_hosts as _resolve_public_hosts,
)
from app.executors.base import ToolExecutor

_MAX_BYTES = 2 * 1024 * 1024
_MAX_TEXT_CHARS = 8000
_MAX_REDIRECTS = 3
_TIMEOUT_SECONDS = 15.0
_USER_AGENT = "AgentWorkbench/0.1 (+read_url)"
# 一次解析最多尝试几个地址（域名可能同时有 A/AAAA 记录）。
_MAX_CONNECT_ATTEMPTS = 2
# 一轮地址全部连不上时重新解析再试：DNS 轮换、CDN 边缘节点下线都会让上一次解析
# 的地址临时不可用。每轮都重新校验、连的仍是当轮校验过的地址，所以重试不会重开
# DNS rebinding 的口子。
_RESOLVE_ROUNDS = 2


class FetchError(RuntimeError):
    """抓取失败（网络、策略或内容问题）。"""


def _is_blocked_ip(raw: str) -> bool:
    """保留旧名（内部与测试按它断言），实现见 core.net_guard。"""
    return is_blocked_ip(raw)


async def resolve_public_hosts(host: str) -> list[str]:
    """委托 core.net_guard，异常换成 FetchError（调用方按 FetchError 处理）。"""
    try:
        return await _resolve_public_hosts(host)
    except NetGuardError as exc:
        raise FetchError(str(exc)) from exc


async def _assert_public_host(host: str) -> None:
    """域名/IP 校验：任一解析结果落在内网即拒绝。"""
    await resolve_public_hosts(host)


def _normalize_url(raw: str) -> str:
    """委托 core.net_guard.normalize_http_url（异常换成 FetchError）。

    为什么不再本地实现：018 的 webhook 也要同一套 scheme 判定，
    两处各写一份必然分叉（`javascript:` 那种没有 `://` 的写法最容易漏）。
    """
    try:
        return normalize_http_url(raw)
    except NetGuardError as exc:
        raise FetchError(str(exc)) from exc


_SCRIPT_STYLE = re.compile(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>")
_BLOCK_TAG = re.compile(r"(?i)</?(p|div|br|li|tr|h[1-6]|section|article)[^>]*>")
_TAG = re.compile(r"(?s)<[^>]+>")
_SPACES = re.compile(r"[ \t\u00a0]+")
_NEWLINES = re.compile(r"\n{3,}")


def html_to_text(html: str) -> str:
    """极简 HTML → 文本（不引入解析库，够用于给 LLM 读正文）。"""
    text = _SCRIPT_STYLE.sub(" ", html)
    text = _BLOCK_TAG.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _NEWLINES.sub("\n\n", text).strip()


def _authority(parsed: Any) -> str:
    """Host 头：原始域名 + 显式端口（不含 userinfo）。"""
    host = parsed.hostname or ""
    if ":" in host:  # IPv6 字面量
        host = f"[{host}]"
    return f"{host}:{parsed.port}" if parsed.port else host


def _connect_url(url: str, address: str) -> str:
    """把 URL 的主机替换成已校验的 IP；路径/查询/端口保持不变。"""
    parsed = urlparse(url)
    host = f"[{address}]" if ":" in address else address
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return parsed._replace(netloc=netloc).geturl()


class _Fetched:
    """一跳的结果：重定向只带 location，正文已按上限截断。"""

    __slots__ = ("body", "encoding", "headers", "location", "status")

    def __init__(
        self, status: int, response: httpx.Response, body: bytes, location: str | None
    ) -> None:
        self.status = status
        self.headers = response.headers
        self.encoding = response.encoding
        self.body = body
        self.location = location


class ReadUrlExecutor(ToolExecutor):
    """read_url：抓取网页并返回正文文本。"""

    name = "read_url"
    _SCHEMA: ClassVar[dict[str, Any]] = {
        "type": "function",
        "function": {
            "name": "read_url",
            "description": (
                "读取指定网页的正文内容（纯文本）。用于需要原文细节、"
                "而搜索摘要不够的场景。仅支持公网 http/https 地址。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "完整网址，如 https://example.com/post",
                    }
                },
                "required": ["url"],
            },
        },
    }

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        resolver: Callable[[str], Awaitable[list[str]]] | None = None,
    ) -> None:
        """Args:
        client: 注入的 HTTP 客户端（测试用 MockTransport，不联网）。
        resolver: 主机解析 + 公网校验（默认 resolve_public_hosts）。测试可注入
            简化版，把「抓取逻辑」与「SSRF 策略」分开验证。
        """
        self._client = client
        self._resolver = resolver or resolve_public_hosts

    def schema(self) -> dict[str, Any]:
        return self._SCHEMA

    def _new_client(self) -> httpx.AsyncClient:
        """每跳一个客户端：不复用连接。

        连接是按「已校验的 IP」建立的，而 TLS 身份是 Host/SNI 里的域名。如果我
        复用连接，两个解析到同一 IP 的域名（CDN、共享主机）会共用第一次握手建立
        的 TLS 会话，第二个域名的请求就跑在别人的证书上。单次抓取最多 4 跳，多一
        次握手的代价可以接受。
        """
        return httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=False,  # 手动跟随：每跳都要过 SSRF 校验
            headers={"User-Agent": _USER_AGENT},
            limits=httpx.Limits(max_keepalive_connections=0),
        )

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> _Fetched:
        """解析一次、连那个 IP、边读边判上限；一轮连不上就重新解析再试一次。"""
        parsed = urlparse(url)
        host = parsed.hostname or ""
        host_header = _authority(parsed)
        extensions = {"sni_hostname": host} if parsed.scheme == "https" else {}
        last_error: Exception | None = None
        async with asyncio.timeout(_TIMEOUT_SECONDS):  # 整跳（含解析与重试）的总预算
            for _round in range(_RESOLVE_ROUNDS):
                addresses = (await self._resolver(host))[:_MAX_CONNECT_ATTEMPTS]
                if not addresses:
                    raise FetchError(f"域名解析失败: {host}")
                for address in addresses:
                    try:
                        return await self._stream(
                            client, _connect_url(url, address), host_header, extensions
                        )
                    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                        # 只有「连不上这个地址」才换下一个；读超时换地址只会把时间翻倍
                        last_error = exc
                        continue
        if last_error is not None:
            raise last_error
        raise FetchError("没有可连接的地址")

    async def _stream(
        self,
        client: httpx.AsyncClient,
        target: str,
        host_header: str,
        extensions: dict[str, Any],
    ) -> _Fetched:
        """一次具体的请求：Host/SNI 是原域名，连接目标是已校验的地址。"""
        async with client.stream(
            "GET",
            target,
            headers={"Host": host_header},
            extensions=extensions,
        ) as response:
            if response.is_redirect:
                # 不读 body：重定向只需要 Location
                return _Fetched(
                    response.status_code,
                    response,
                    b"",
                    response.headers.get("location", ""),
                )
            if response.status_code >= 400:
                return _Fetched(response.status_code, response, b"", None)
            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                budget = _MAX_BYTES - len(buffer)
                if len(chunk) >= budget:
                    # 超出上限就停止下载：不把整个 body 读进内存
                    buffer.extend(chunk[:budget])
                    break
                buffer.extend(chunk)
            return _Fetched(response.status_code, response, bytes(buffer), None)

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = _normalize_url(str(arguments.get("url") or arguments.get("input") or ""))
        try:
            current = url
            for _ in range(_MAX_REDIRECTS + 1):
                client = self._client or self._new_client()
                try:
                    fetched = await self._fetch(client, current)
                finally:
                    if self._client is None:
                        await client.aclose()
                if fetched.location is None:
                    break
                if not fetched.location:
                    raise FetchError("重定向缺少 Location")
                current = _normalize_url(str(httpx.URL(current).join(fetched.location)))
            else:
                raise FetchError(f"重定向次数超过 {_MAX_REDIRECTS}")

            if fetched.status >= 400:
                return {
                    "url": current,
                    "status": fetched.status,
                    "error": f"HTTP {fetched.status}",
                }

            raw = fetched.body
            encoding = fetched.encoding or "utf-8"
            try:
                body = raw.decode(encoding, errors="replace")
            except LookupError:
                body = raw.decode("utf-8", errors="replace")

            content_type = fetched.headers.get("content-type", "")
            text = body if "html" not in content_type.lower() else html_to_text(body)
            truncated = len(text) > _MAX_TEXT_CHARS
            return {
                "url": current,
                "status": fetched.status,
                "content_type": content_type.split(";")[0],
                "text": text[:_MAX_TEXT_CHARS],
                "truncated": truncated,
                "body_truncated": len(raw) >= _MAX_BYTES,
            }
        except TimeoutError:
            # asyncio.timeout 的总预算耗尽（包含换地址/重新解析的重试）
            return {"url": url, "error": f"抓取超时（上限 {int(_TIMEOUT_SECONDS)}s）"}
        except httpx.HTTPError as exc:
            return {"url": url, "error": f"抓取失败: {exc}"}
        except FetchError as exc:
            return {"url": url, "error": str(exc)}
