"""进程级 Redis 单例：同一事件循环复用连接池。

回归点（2026-11-17 审计）：token_store / login_limiter / events 原先各自
`from_url()` 用完 `aclose()`，一次登录要建 3 条连接、每条 run 事件 1 条。
"""

from __future__ import annotations

import asyncio

import pytest

from app.core import redis as redis_mod


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    calls: list[dict] = []

    def fake_from_url(url, **kwargs):
        client = object()
        calls.append({"url": url, **kwargs})
        return client

    monkeypatch.setattr(redis_mod.aioredis, "from_url", fake_from_url)
    monkeypatch.setattr(redis_mod, "_client", None)
    monkeypatch.setattr(redis_mod, "_client_loop", None)
    yield calls
    redis_mod._client = None
    redis_mod._client_loop = None


def test_same_loop_reuses_one_client(_reset_singleton):
    calls = _reset_singleton

    async def scenario():
        first = redis_mod.get_redis()
        second = redis_mod.get_redis()
        return first, second

    first, second = asyncio.run(scenario())

    assert first is second
    assert len(calls) == 1


def test_client_carries_connect_timeout_and_decoding(_reset_singleton):
    calls = _reset_singleton

    asyncio.run(_get())

    assert calls[0]["decode_responses"] is True
    assert calls[0]["socket_connect_timeout"] == 2.0


async def _get():
    redis_mod.get_redis()


def test_new_event_loop_gets_its_own_client(_reset_singleton):
    """`asyncio.run` 每次新建 loop：必须换客户端，否则会踩跨 loop 复用。"""
    calls = _reset_singleton

    async def scenario():
        return redis_mod.get_redis()

    first = asyncio.run(scenario())
    second = asyncio.run(scenario())

    assert first is not second
    assert len(calls) == 2


def test_close_resets_singleton(_reset_singleton):
    calls = _reset_singleton

    async def scenario():
        redis_mod.get_redis()
        await redis_mod.close_redis()
        return redis_mod._client

    assert asyncio.run(scenario()) is None
    assert len(calls) == 1
