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

import redis.asyncio as aioredis
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_KEY_PREFIX = "refresh:"


def _key(jti: str) -> str:
    return f"{_KEY_PREFIX}{jti}"


def _client() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def register_refresh_token(jti: str, user_id: str, ttl_seconds: int) -> None:
    """登记 refresh token 的 jti（TTL 与 token 有效期一致）。"""
    client = _client()
    try:
        await client.set(_key(jti), user_id, ex=max(ttl_seconds, 1))
    except Exception as exc:  # noqa: BLE001 - 存储不可用不应阻断登录
        logger.warning("refresh_store_unavailable", op="register", error=type(exc).__name__)
    finally:
        await client.aclose()


async def consume_refresh_token(jti: str) -> bool:
    """一次性消费 jti：True=有效（已消费），False=已使用/已撤销/不存在。

    Redis 异常时返回 True（fail-open，见模块 docstring）。
    """
    client = _client()
    try:
        # GETDEL 原子：并发用同一 token 时只有一个能取到
        return (await client.getdel(_key(jti))) is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("refresh_store_unavailable", op="consume", error=type(exc).__name__)
        return True
    finally:
        await client.aclose()


async def revoke_all_refresh_tokens(user_id: str) -> int:
    """撤销某用户的全部 refresh token（改密/禁用账号时调用），返回撤销数量。

    注意：按 jti 存储无法反查 user，这里用 SCAN 遍历匹配 value。
    用户量增大后应改为「按用户维护 jti 集合」，当前规模（内部工作台）可接受。
    """
    client = _client()
    revoked = 0
    try:
        async for key in client.scan_iter(match=f"{_KEY_PREFIX}*", count=200):
            if await client.get(key) == user_id:
                await client.delete(key)
                revoked += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("refresh_store_unavailable", op="revoke", error=type(exc).__name__)
    finally:
        await client.aclose()
    return revoked
