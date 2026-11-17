"""Refresh token 轮换存储（Redis）。

问题：refresh token 是无状态 JWT，签发后直到过期（默认 7 天）都有效。
一旦泄露，攻击者可长期续命——轮换（rotation）只改客户端持有的 token，
服务端不认旧 token 才算真正失效。

做法：
- 签发 refresh token 时把 jti 写入 Redis（`refresh:{jti}` → user_id，TTL = refresh 有效期）
- 刷新时一次性消费（GETDEL 原子删除）：取不到 = 已用过/已撤销 → 401
- 旧 token 立即失效，泄露窗口从「7 天」缩短为「一次刷新」

可用性权衡：Redis 不可用时 **放行**（fail-open）并记 warning。
理由——登录本身要密码，refresh 泄露的前提是攻击者已拿到 token；
若 Redis 抖动就让全体用户掉线，可用性损失大于收益。
"""

import structlog

from app.core.redis import get_redis

logger = structlog.get_logger(__name__)

_KEY_PREFIX = "refresh:"
_GRACE_PREFIX = "refresh_grace:"
# 每个用户一个 jti 索引集合：撤销时按集合删除，不必全库 SCAN
_USER_PREFIX = "refresh_user:"


def _key(jti: str) -> str:
    return f"{_KEY_PREFIX}{jti}"


def _grace_key(jti: str) -> str:
    return f"{_GRACE_PREFIX}{jti}"


def _user_key(user_id: str) -> str:
    return f"{_USER_PREFIX}{user_id}"


async def register_refresh_token(jti: str, user_id: str, ttl_seconds: int) -> None:
    """登记 refresh token 的 jti（TTL 与 token 有效期一致）。

    同时写进用户维度的索引集合：撤销时按 `SMEMBERS` 精确删除 N 个 key，
    而不是全库 `SCAN` 再逐个 `GET` 比对（旧做法是 O(全部 refresh token)）。
    """
    ttl = max(ttl_seconds, 1)
    try:
        client = get_redis()
        async with client.pipeline(transaction=False) as pipe:
            pipe.set(_key(jti), user_id, ex=ttl)
            pipe.sadd(_user_key(user_id), jti)
            # 集合 TTL 跟着 jti 走：最后一个 jti 过期后集合自然消失，不会长期堆积
            pipe.expire(_user_key(user_id), ttl)
            await pipe.execute()
    except Exception as exc:  # noqa: BLE001 - 存储不可用不应阻断登录
        logger.warning(
            "refresh_store_unavailable", op="register", error=type(exc).__name__
        )


async def consume_refresh_token(jti: str, *, grace_seconds: int = 0) -> bool:
    """消费 jti：True=有效，False=已使用/已撤销/不存在。

    宽限期（grace_seconds）解决多标签页互相踢：两个标签页同时刷新时，后到的
    那个手里还是旧 token。宽限期内允许重复消费（各拿一对新 token，都能用），
    超过宽限期一律拒绝——重放窗口从「7 天」收紧到「秒级」。

    Redis 异常时返回 True（fail-open，见模块 docstring）。
    """
    client = get_redis()
    try:
        # 宽限期内：首次消费时留下的标记，允许再次使用
        if grace_seconds > 0 and await client.exists(_grace_key(jti)):
            return True

        # GETDEL 原子：并发用同一 token 时只有一个能取到主记录
        owner = await client.getdel(_key(jti))
        if owner is None:
            return False
        # 顺手从用户索引里摘掉，避免集合里堆已消费的 jti
        await client.srem(_user_key(str(owner)), jti)

        if grace_seconds > 0:
            await client.set(_grace_key(jti), "1", ex=grace_seconds)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "refresh_store_unavailable", op="consume", error=type(exc).__name__
        )
        return True


async def revoke_all_refresh_tokens(user_id: str) -> int:
    """撤销某用户的全部 refresh token（改密/禁用账号时调用），返回撤销数量。

    主路径走用户索引集合 `refresh_user:{user_id}`：一次 SMEMBERS + 一次 DEL，
    成本与「该用户有多少个 refresh token」成正比，与全库规模无关。
    旧做法是全库 SCAN 再逐个 GET 比对，用户越多越慢。
    """
    client = get_redis()
    revoked = 0
    try:
        jtis = {str(jti) for jti in await client.smembers(_user_key(user_id))}
        if jtis:
            revoked = int(await client.delete(*[_key(jti) for jti in jtis]))
        await client.delete(_user_key(user_id))
        if jtis:
            return revoked
        # 兼容升级前签发的 token（那时还没有用户索引）：只在索引为空时回退到 SCAN
        async for key in client.scan_iter(match=f"{_KEY_PREFIX}*", count=200):
            if await client.get(key) == user_id:
                await client.delete(key)
                revoked += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "refresh_store_unavailable", op="revoke", error=type(exc).__name__
        )
    return revoked
