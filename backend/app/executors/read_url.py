"""read_url 工具（真实实现）：抓取网页正文。

此前是 NoopExecutor 占位——LLM 调 read_url 拿到「占位」提示，会把"读过网页"
写进结论，造成看似有依据实则编造的输出。

安全约束（LLM 可控的 URL 是典型的 SSRF 入口）：
- 仅 http/https
- 解析后的 IP 必须在公网（拒绝 127/10/172.16/192.168/169.254 元数据地址等）
- 重定向手动跟随，每跳重新校验（防止 302 跳到内网）
- 响应体上限 2MB、总超时 15s
"""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from app.executors.base import ToolExecutor

_MAX_BYTES = 2 * 1024 * 1024
_MAX_TEXT_CHARS = 8000
_MAX_REDIRECTS = 3
_TIMEOUT_SECONDS = 15.0
_USER_AGENT = "AgentWorkbench/0.1 (+read_url)"

_BLOCKED_NETS = tuple(
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


class FetchError(RuntimeError):
    """抓取失败（网络、策略或内容问题）。"""


def _is_blocked_ip(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return True  # 解析不出 IP 一律视为不安全
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in _BLOCKED_NETS)


async def _assert_public_host(host: str) -> None:
    """域名/IP 校验：任一解析结果落在内网即拒绝。"""
    if not host:
        raise FetchError("URL 缺少主机名")

    try:
        resolved = [host] if ipaddress.ip_address(host) else []
    except ValueError:
        resolved = []

    if not resolved:
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except OSError as exc:
            raise FetchError(f"域名解析失败: {host}") from exc
        resolved = sorted({str(info[4][0]) for info in infos})

    for raw_ip in resolved:
        if _is_blocked_ip(raw_ip):
            raise FetchError(f"目标地址不允许访问（内网/保留地址）: {host}")


def _normalize_url(raw: str) -> str:
    """校验 scheme 并补全协议。

    注意不能只看 "://"：`javascript:alert(1)` 没有 "://"，若直接补 https
    就绕过了 scheme 检查。
    """
    url = (raw or "").strip()
    if not url:
        raise FetchError("URL 为空")

    if "://" in url:
        scheme = url.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            raise FetchError(f"仅支持 http/https，收到: {scheme or '空'}")
    else:
        head = url.split("/", 1)[0]
        if ":" in head:
            _, _, port = head.partition(":")
            if not port.isdigit():
                raise FetchError(f"仅支持 http/https，收到: {head.split(':', 1)[0]}")
        url = f"https://{url}"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"仅支持 http/https，收到: {parsed.scheme or '空'}")
    return url


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
                    "url": {"type": "string", "description": "完整网址，如 https://example.com/post"}
                },
                "required": ["url"],
            },
        },
    }

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        host_check: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Args:
        client: 注入的 HTTP 客户端（测试用 MockTransport，不联网）。
        host_check: 主机安全校验（默认 _assert_public_host）。测试可注入简化版，
            把「抓取逻辑」与「SSRF 策略」分开验证。
        """
        self._client = client
        self._host_check = host_check or _assert_public_host

    def schema(self) -> dict[str, Any]:
        return self._SCHEMA

    def _make_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=False,  # 手动跟随：每跳都要过 SSRF 校验
            headers={"User-Agent": _USER_AGENT},
        )

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = _normalize_url(str(arguments.get("url") or arguments.get("input") or ""))
        client = self._make_client()
        owns_client = self._client is None
        try:
            current = url
            for _ in range(_MAX_REDIRECTS + 1):
                await self._host_check(urlparse(current).hostname or "")
                resp = await client.get(current)
                if resp.is_redirect:
                    location = resp.headers.get("location", "")
                    if not location:
                        raise FetchError("重定向缺少 Location")
                    current = _normalize_url(str(resp.url.join(location)))
                    continue
                break
            else:
                raise FetchError(f"重定向次数超过 {_MAX_REDIRECTS}")

            if resp.status_code >= 400:
                return {
                    "url": current,
                    "status": resp.status_code,
                    "error": f"HTTP {resp.status_code}",
                }

            raw = resp.content[:_MAX_BYTES]
            encoding = resp.encoding or "utf-8"
            try:
                body = raw.decode(encoding, errors="replace")
            except LookupError:
                body = raw.decode("utf-8", errors="replace")

            content_type = resp.headers.get("content-type", "")
            text = body if "html" not in content_type.lower() else html_to_text(body)
            truncated = len(text) > _MAX_TEXT_CHARS
            return {
                "url": str(resp.url),
                "status": resp.status_code,
                "content_type": content_type.split(";")[0],
                "text": text[:_MAX_TEXT_CHARS],
                "truncated": truncated,
            }
        except httpx.HTTPError as exc:
            return {"url": url, "error": f"抓取失败: {exc}"}
        except FetchError as exc:
            return {"url": url, "error": str(exc)}
        finally:
            if owns_client:
                await client.aclose()
