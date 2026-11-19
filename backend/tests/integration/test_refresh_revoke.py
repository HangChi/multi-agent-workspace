"""refresh token 索引与撤销：按用户集合精确删除，不再全库 SCAN。

回归点（2026-11-17 审计）：`revoke_all_refresh_tokens` 原先遍历全库
`refresh:*` 再逐个 GET 比对 value —— 成本与**所有用户**的 refresh token 数成正比。
改密/禁用账号是安全响应动作，越慢越糟。

用真实 Redis（测试环境本来就需要它）：这套逻辑的价值就在于管道、过期与
集合语义的配合，用假对象测等于测自己写的假对象。
"""

from __future__ import annotations

import uuid

import pytest

from app.core import token_store

pytestmark = pytest.mark.integration


def _ids() -> tuple[str, str]:
    return f"jti-{uuid.uuid4().hex}", f"user-{uuid.uuid4().hex}"


async def test_register_then_consume_removes_from_user_index():
    jti, user_id = _ids()

    await token_store.register_refresh_token(jti, user_id, ttl_seconds=60)
    client = token_store.get_redis()
    assert jti in {
        str(item) for item in await client.smembers(f"refresh_user:{user_id}")
    }

    assert await token_store.consume_refresh_token(jti) is True
    assert jti not in {
        str(item) for item in await client.smembers(f"refresh_user:{user_id}")
    }


async def test_consume_is_one_shot():
    jti, user_id = _ids()
    await token_store.register_refresh_token(jti, user_id, ttl_seconds=60)

    assert await token_store.consume_refresh_token(jti) is True
    assert await token_store.consume_refresh_token(jti) is False


async def test_revoke_all_deletes_every_token_of_that_user_only():
    jti_a, user_id = _ids()
    jti_b, _ = _ids()
    _, other_user = _ids()
    other_jti, _ = _ids()

    await token_store.register_refresh_token(jti_a, user_id, ttl_seconds=60)
    await token_store.register_refresh_token(jti_b, user_id, ttl_seconds=60)
    await token_store.register_refresh_token(other_jti, other_user, ttl_seconds=60)

    revoked = await token_store.revoke_all_refresh_tokens(user_id)

    assert revoked == 2
    assert await token_store.consume_refresh_token(jti_a) is False
    assert await token_store.consume_refresh_token(jti_b) is False
    # 其他用户的 token 不受影响
    assert await token_store.consume_refresh_token(other_jti) is True


async def test_revoke_all_on_clean_user_is_noop():
    _, user_id = _ids()
    assert await token_store.revoke_all_refresh_tokens(user_id) == 0


async def test_legacy_token_without_index_is_still_revoked():
    """升级前的 token 没有用户索引：索引为空时回退 SCAN，保证撤销不漏。"""
    legacy_jti, user_id = _ids()
    client = token_store.get_redis()
    await client.set(f"refresh:{legacy_jti}", user_id, ex=60)  # 只写主记录，不写索引

    revoked = await token_store.revoke_all_refresh_tokens(user_id)

    assert revoked == 1
    assert await token_store.consume_refresh_token(legacy_jti) is False


# ---------- 015 覆盖率补齐：Redis 不可用时的 fail-open 降级 ----------


class _BoomRedis:
    """同步 client，所有访问方法抛异常（模拟 Redis 抖动/宕机）。

    注意 `get_redis()` 本身是同步的，且在 consume/revoke 里位于 try 之外——
    所以要让「取到 client 之后、调用方法时」才抛，才能走到对应的降级分支。
    """

    def pipeline(self, **kwargs):
        raise ConnectionError("redis down")

    async def exists(self, *_a, **_k):
        raise ConnectionError("redis down")

    async def getdel(self, *_a, **_k):
        raise ConnectionError("redis down")

    async def smembers(self, *_a, **_k):
        raise ConnectionError("redis down")

    async def scan_iter(self, *_a, **_k):
        raise ConnectionError("redis down")


async def test_register_fails_open_when_redis_unavailable(monkeypatch):
    """Redis 不可用时注册放行：登录不能因为 token 存储抖动而失败。

    fail-open 的理由是——登录本身要密码，refresh 泄露的前提是攻击者已拿到
    token；若 Redis 抖动就让全体用户掉线，可用性损失大于收益。
    """
    from app.core import token_store as ts

    monkeypatch.setattr(ts, "get_redis", lambda: _BoomRedis())
    # 不应抛异常
    await ts.register_refresh_token("jti-x", "user-x", ttl_seconds=60)


async def test_consume_fails_open_on_redis_error(monkeypatch):
    """消费路径 Redis 异常时返回 True（视为可用），避免把抖动当成重放攻击。"""
    from app.core import token_store as ts

    monkeypatch.setattr(ts, "get_redis", lambda: _BoomRedis())
    assert await ts.consume_refresh_token("jti-y") is True


async def test_revoke_all_fails_open_on_redis_error(monkeypatch):
    """撤销路径 Redis 异常时不抛错：登出必须幂等，不能让客户端卡在报错里。"""
    from app.core import token_store as ts

    monkeypatch.setattr(ts, "get_redis", lambda: _BoomRedis())
    assert await ts.revoke_all_refresh_tokens("user-z") == 0
