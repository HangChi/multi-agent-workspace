"""Distributed concurrency leases for external MCP calls."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import redis

from app.core.config import settings

_LEASE_GRACE_SECONDS = 120


def _redis_client() -> Any:
    return redis.from_url(settings.redis_url, decode_responses=True)


def _server_key(server_identity: str) -> str:
    digest = hashlib.sha256(server_identity.encode("utf-8")).hexdigest()[:32]
    return f"mcp:concurrency:{digest}"


def _try_acquire(server_identity: str) -> str | None:
    client = _redis_client()
    try:
        key_prefix = _server_key(server_identity)
        ttl = max(1, int(settings.mcp_call_timeout_seconds) + _LEASE_GRACE_SECONDS)
        for slot in range(settings.mcp_max_concurrency_per_server):
            token = f"{uuid.uuid4()}:{slot}:{time.monotonic_ns()}"
            if client.set(f"{key_prefix}:{slot}", token, nx=True, ex=ttl):
                return token
        return None
    finally:
        client.close()


def release_slot(server_identity: str, token: str | None) -> None:
    """Release only the slot owned by this lease; never delete a replacement lease."""
    if not token:
        return
    try:
        slot = token.split(":", 2)[1]
    except (IndexError, AttributeError):
        return
    client = _redis_client()
    try:
        client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            f"{_server_key(server_identity)}:{slot}",
            token,
        )
    finally:
        client.close()


async def acquire_slot(
    server_identity: str,
    *,
    wait_seconds: float = 0.0,
    abort_check: Callable[[], Awaitable[bool]] | None = None,
) -> str | None:
    """Acquire a bounded per-server lease, optionally waiting until a slot frees."""
    deadline = asyncio.get_running_loop().time() + max(0.0, wait_seconds)
    while True:
        if abort_check is not None and await abort_check():
            return None
        lease = await asyncio.to_thread(_try_acquire, server_identity)
        if lease is not None:
            return lease
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(min(0.05, max(0.0, deadline - asyncio.get_running_loop().time())))
