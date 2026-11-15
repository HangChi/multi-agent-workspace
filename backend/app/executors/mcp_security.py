"""Outbound endpoint validation for administrator-registered MCP servers."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from app.core.config import settings

Resolver = Callable[..., Sequence[tuple]]


@dataclass(frozen=True)
class ValidatedEndpoint:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


def _validate_address(value: str) -> str:
    address = ipaddress.ip_address(value.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_multicast or address.is_reserved or not address.is_global:
        raise ValueError("endpoint resolves to a prohibited network address")
    return str(address)


def validate_endpoint(
    url: str,
    *,
    resolver: Resolver = socket.getaddrinfo,
    redirect_url: str | None = None,
    allowed_ports: set[int] | None = None,
    require_https: bool | None = None,
) -> ValidatedEndpoint:
    """Validate URL syntax and all resolved IPs; never disclose internal targets.

    Redirects are rejected by default. If a redirect target is supplied by a
    caller that follows redirects, this function recursively validates it.
    Production callers should keep redirects disabled at the HTTP client too.
    """
    if redirect_url is not None:
        # Validate the target first so a dangerous redirect never gets followed.
        try:
            validate_endpoint(
                redirect_url,
                resolver=resolver,
                allowed_ports=allowed_ports,
                require_https=require_https,
            )
        except ValueError as exc:
            raise ValueError(f"redirect target rejected: {exc}") from exc
        raise ValueError("redirect responses are not permitted")

    parsed = urlsplit(url)
    https_required = settings.mcp_require_https if require_https is None else require_https
    if parsed.scheme.lower() != "https" and https_required:
        raise ValueError("MCP endpoint must use HTTPS")
    if parsed.scheme.lower() not in {"https", "http"}:
        raise ValueError("unsupported MCP endpoint scheme")
    if parsed.username or parsed.password:
        raise ValueError("MCP endpoint URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("MCP endpoint URL must not contain query or fragment data")
    if not parsed.hostname:
        raise ValueError("MCP endpoint URL must include a hostname")

    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("invalid MCP endpoint host or port") from exc
    port_allowlist = allowed_ports or {
        int(part.strip()) for part in settings.mcp_allowed_ports.split(",") if part.strip()
    }
    if port not in port_allowlist:
        raise ValueError("MCP endpoint port is not allowed")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    addresses: tuple[str, ...]
    if literal is not None:
        addresses = (_validate_address(str(literal)),)
    else:
        try:
            records = resolver(host, port, type=socket.SOCK_STREAM)
            addresses = tuple(dict.fromkeys(_validate_address(record[4][0]) for record in records))
        except (OSError, ValueError, IndexError, TypeError) as exc:
            raise ValueError("MCP endpoint DNS validation failed") from exc
        if not addresses:
            raise ValueError("MCP endpoint has no usable address")

    normalized_netloc = f"[{host}]" if ":" in host else host
    if port != (443 if parsed.scheme.lower() == "https" else 80):
        normalized_netloc = f"{normalized_netloc}:{port}"
    normalized = urlunsplit((parsed.scheme.lower(), normalized_netloc, parsed.path or "/", "", ""))
    return ValidatedEndpoint(url=normalized, host=host, port=port, addresses=addresses)
