"""登录防爆破限流（Redis 计数器）。

问题：登录端点此前没有任何频率限制，密码可被无限次枚举。叠加 bcrypt 的高
CPU 成本，攻击者用几条并发连接就能同时完成「猜密码」和「拖慢全站」。

策略（双层，任一超限即拒绝）：
1. **按邮箱+IP**：同一来源对同一账号的连续失败，阈值低、窗口短 —— 挡住针对
   单个账号的定向爆破，同时不影响正常用户手滑打错。
2. **按 IP**：同一来源的总失败次数，阈值高、窗口长 —— 挡住「换邮箱喷洒」的
   撞库（每次换一个账号试，单账号计数抓不到）。

只对**失败**计数，登录成功即清零（按邮箱+IP）。这样：
- 正常用户连续登录成功永远不会被限；
- 打错一次密码不会留下长期影响；
- 攻击者必须真的付出失败次数才会被拦。

可用性权衡：限流是**纵深防御**，不是主认证手段。所有异常一律吞掉并放行
（fail-open）——Redis 抖动让全员无法登录，损失远大于收益，与 `token_store`
的取舍保持一致。唯一的例外是主动抛出的 `AppError`（429），它代表「确实超限」。
"""

import redis.asyncio as aioredis
import structlog

from app.api.errors import AppError
from app.core.config import settings

logger = structlog.get_logger(__name__)

_ACCOUNT_PREFIX = "login_fail:acct:"
_IP_PREFIX = "login_fail:ip:"


def _client() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


def _account_key(email: str, ip: str) -> str:
    """邮箱统一小写，避免 A@x.com / a@x.com 各记一份绕过阈值。"""
    return f"{_ACCOUNT_PREFIX}{email.strip().lower()}|{ip}"


def _ip_key(ip: str) -> str:
    return f"{_IP_PREFIX}{ip}"


async def _safe_close(client: aioredis.Redis) -> None:
    """关闭连接；关闭失败也不该影响主流程。"""
    try:
        await client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("login_limiter_close_failed", error=type(exc).__name__)


async def check_login_allowed(*, email: str, ip: str) -> None:
    """登录前检查。超限抛 429（RATE_LIMITED）；存储异常放行。

    先查后计（不是先计后查）：第 N 次失败会在下一次请求被拦，
    不会出现「正在被爆破的账号反而先被锁」的误判顺序问题。
    """
    client = _client()
    try:
        acct_raw = await client.get(_account_key(email, ip))
        if acct_raw is not None and int(acct_raw) >= settings.login_max_fails_per_account:
            logger.warning("login_rate_limited", scope="account", ip=ip[:64])
            raise AppError(
                "RATE_LIMITED",
                f"该账号登录失败次数过多，请 {settings.login_lockout_minutes} 分钟后再试",
            )

        ip_raw = await client.get(_ip_key(ip))
        if ip_raw is not None and int(ip_raw) >= settings.login_max_fails_per_ip:
            logger.warning("login_rate_limited", scope="ip", ip=ip[:64])
            raise AppError(
                "RATE_LIMITED",
                f"该来源登录失败次数过多，请 {settings.login_lockout_minutes} 分钟后再试",
            )
    except AppError:
        # 确实超限：必须让调用方看到 429，不能当成「存储不可用」放行
        raise
    except Exception as exc:  # noqa: BLE001 - 见模块 docstring：不可用即放行
        # 不能只捕 RedisError：客户端对象本身异常（AttributeError 等）
        # 同样会穿透成登录 500，而限流绝不该成为登录的单点故障。
        logger.warning("login_limiter_unavailable", op="check", error=type(exc).__name__)
    finally:
        await _safe_close(client)


async def record_login_failure(*, email: str, ip: str) -> None:
    """记录一次登录失败（两个计数器各 +1，并设置过期）。"""
    client = _client()
    ttl = settings.login_lockout_minutes * 60
    try:
        # pipeline 一次往返完成两组 INCR+EXPIRE
        async with client.pipeline(transaction=False) as pipe:
            pipe.incr(_account_key(email, ip))
            pipe.expire(_account_key(email, ip), ttl)
            pipe.incr(_ip_key(ip))
            pipe.expire(_ip_key(ip), ttl)
            await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("login_limiter_unavailable", op="record", error=type(exc).__name__)
    finally:
        await _safe_close(client)


async def reset_login_failures(*, email: str, ip: str) -> None:
    """登录成功后清掉该账号+IP 的失败计数（IP 级总计数不清，避免掩蔽撞库）。"""
    client = _client()
    try:
        await client.delete(_account_key(email, ip))
    except Exception as exc:  # noqa: BLE001
        logger.warning("login_limiter_unavailable", op="reset", error=type(exc).__name__)
    finally:
        await _safe_close(client)
