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
