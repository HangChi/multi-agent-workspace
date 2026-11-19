import socket

import pytest

from app.executors.mcp_security import validate_endpoint


def _resolver(*_args, **_kwargs):
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://tools.example.test/mcp",
        "https://user:pass@tools.example.test/mcp",
        "https://tools.example.test/mcp?token=secret",
        "https://127.0.0.1/mcp",
        "https://tools.example.test:8443/mcp",
    ],
)
def test_rejects_unsafe_endpoint_forms(url):
    with pytest.raises(ValueError):
        validate_endpoint(url, resolver=_resolver)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "169.254.169.254",
        "::1",
        "fc00::1",
        "fe80::1",
        "224.0.0.1",
    ],
)
def test_rejects_non_public_dns_answers(address):
    def resolver(*_args, **_kwargs):
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [
            (
                family,
                socket.SOCK_STREAM,
                6,
                "",
                (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443),
            )
        ]

    with pytest.raises(ValueError):
        validate_endpoint("https://tools.example.test/mcp", resolver=resolver)


def test_normalizes_public_https_endpoint():
    endpoint = validate_endpoint("https://TOOLS.example.test/mcp", resolver=_resolver)
    assert endpoint.url == "https://tools.example.test/mcp"
    assert endpoint.host == "tools.example.test"
    assert endpoint.addresses == ("93.184.216.34",)


def test_rejects_redirect_targets_before_following():
    with pytest.raises(ValueError, match="redirect"):
        validate_endpoint(
            "https://tools.example.test/mcp",
            resolver=_resolver,
            redirect_url="http://127.0.0.1/",
        )


# ---------- 015 覆盖率补齐：SSRF 边界逐条钉死 ----------

from app.executors.mcp_security import _validate_address, ValidatedEndpoint  # noqa: E402


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # IPv4-mapped IPv6 必须先还原成 IPv4 再判定：::ffff:127.0.0.1 的
        # is_global 为 True（IPv6 视角），直接判会把回环地址放行——这是
        # SSRF 绕过里最经典的一招。
        ("::ffff:127.0.0.1", ValueError),
        ("::ffff:8.8.8.8", "8.8.8.8"),
        # scope id 后缀（fe80::1%eth0）必须先剥掉再解析，否则 ip_address 直接抛
        ("8.8.8.8%eth0", "8.8.8.8"),
        ("2001:4860:4860::8888", "2001:4860:4860::8888"),
    ],
)
def test_validate_address_handles_encoded_and_mapped_forms(raw, expected):
    """地址归一化：映射地址还原、zone id 剥离、私有段拒绝。

    `8.8.8.8%eth0` 这种形态在 Java/Go 生态的解析器里被接受，因此攻击者会试；
    `ip_address` 会判定非法，所以必须先 split("%")。
    """
    if expected is ValueError:
        with pytest.raises(ValueError):
            _validate_address(raw)
        return
    assert _validate_address(raw) == expected


def test_validate_endpoint_allows_http_when_https_not_required():
    """`require_https=False` 时 http 放行（内部部署/测试用），但仍做 IP 校验。

    注意端口白名单独立于 scheme：默认只放 443，所以放开 http 还必须显式把
    80 加进 `allowed_ports`——这道闸门不是冗余的，它挡住了「同域但非标端口」。
    """
    endpoint = validate_endpoint(
        "http://tools.example.test/mcp",
        resolver=_resolver,
        require_https=False,
        allowed_ports={80},
    )
    assert endpoint.url == "http://tools.example.test/mcp"
    assert endpoint.port == 80


@pytest.mark.parametrize("url", ["ftp://tools.example.test/mcp", "file:///etc/passwd"])
def test_validate_endpoint_rejects_unsupported_scheme(url):
    """scheme 白名单在 HTTPS 检查之后：放开 https 要求才能走到这一条。"""
    with pytest.raises(ValueError, match="scheme"):
        validate_endpoint(url, resolver=_resolver, require_https=False)


def test_validate_endpoint_rejects_non_https_by_default():
    """默认配置必须强制 HTTPS——明文传输会让凭证头在链路上裸奔。"""
    with pytest.raises(ValueError, match="must use HTTPS"):
        validate_endpoint("http://tools.example.test/mcp", resolver=_resolver)


def test_validate_endpoint_rejects_fragment():
    """fragment 同样拒绝：它不会发给服务器，却会出现在日志/审计里被误读。"""
    with pytest.raises(ValueError, match="query or fragment"):
        validate_endpoint("https://tools.example.test/mcp#section", resolver=_resolver)


def test_validate_endpoint_rejects_missing_hostname():
    with pytest.raises(ValueError, match="hostname"):
        validate_endpoint("https:///mcp", resolver=_resolver)


def test_validate_endpoint_rejects_bad_idna_host():
    """超长标签会让 idna 编码失败——必须转成可读错误而不是 UnicodeError 冒到 500。"""

    def _no_resolve(*_a, **_k):
        raise AssertionError("非法主机名不应走到 DNS 解析")

    with pytest.raises(ValueError, match="host or port"):
        validate_endpoint("https://" + "a" * 300 + ".test/mcp", resolver=_no_resolve)


def test_validate_endpoint_rejects_port_outside_allowlist():
    with pytest.raises(ValueError, match="port is not allowed"):
        validate_endpoint(
            "https://tools.example.test:9000/mcp",
            resolver=_resolver,
            allowed_ports={443},
        )


def test_validate_endpoint_honours_custom_allowlist():
    """放行非标端口时，规范化 URL 必须把端口写回去（否则连的是 443）。"""
    endpoint = validate_endpoint(
        "https://tools.example.test:9443/mcp",
        resolver=_resolver,
        allowed_ports={9443},
    )
    assert endpoint.url == "https://tools.example.test:9443/mcp"
    assert endpoint.port == 9443


def test_validate_endpoint_dedupes_and_keeps_address_order():
    """多个 A 记录去重但保持顺序：审计里看到的第一条应当是 DNS 首选。"""

    def resolver(*_a, **_k):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ]

    endpoint = validate_endpoint("https://tools.example.test/mcp", resolver=resolver)
    assert endpoint.addresses == ("93.184.216.34", "93.184.216.35")


def test_validate_endpoint_rejects_when_dns_returns_nothing():
    """空解析结果必须拒绝：否则后续连接会退化成「连 localhost」。"""

    def resolver(*_a, **_k):
        return []

    with pytest.raises(ValueError, match="no usable address"):
        validate_endpoint("https://tools.example.test/mcp", resolver=resolver)


@pytest.mark.parametrize(
    "failure",
    [OSError("dns down"), ValueError("bad record"), IndexError("short tuple")],
)
def test_validate_endpoint_wraps_resolver_errors(failure):
    """解析器抛什么都要收敛成同一条可读错误，且不泄露内网信息。"""

    def resolver(*_a, **_k):
        raise failure

    with pytest.raises(ValueError, match="DNS validation failed"):
        validate_endpoint("https://tools.example.test/mcp", resolver=resolver)


def test_validate_endpoint_accepts_ip_literal_without_dns():
    """IP 字面量不走 DNS，但仍走同一套地址校验。"""

    def _no_resolve(*_a, **_k):
        raise AssertionError("IP 字面量不应触发 DNS 解析")

    endpoint = validate_endpoint("https://93.184.216.34/mcp", resolver=_no_resolve)
    assert endpoint.addresses == ("93.184.216.34",)


def test_validate_endpoint_rejects_private_ip_literal():
    with pytest.raises(ValueError, match="prohibited network address"):
        validate_endpoint("https://10.0.0.1/mcp", resolver=_resolver)


def test_validate_endpoint_normalizes_ipv6_literal_netloc():
    """IPv6 字面量必须加方括号，否则规范化 URL 无法被再次解析。"""
    endpoint = validate_endpoint(
        "https://[2001:4860:4860::8888]/mcp", resolver=_resolver
    )
    assert endpoint.url == "https://[2001:4860:4860::8888]/mcp"


def test_validate_endpoint_default_path_is_root():
    """无路径时补 `/`：否则 urlunsplit 产出 `https://host`，部分客户端会拼错。"""
    endpoint = validate_endpoint("https://tools.example.test", resolver=_resolver)
    assert endpoint.url == "https://tools.example.test/"


def test_redirect_to_safe_target_still_rejected():
    """重定向目标即使安全也必须拒绝——跟随重定向本身就是绕过校验的路径。"""
    with pytest.raises(ValueError, match="not permitted"):
        validate_endpoint(
            "https://tools.example.test/mcp",
            resolver=_resolver,
            redirect_url="https://other.example.test/mcp",
        )
