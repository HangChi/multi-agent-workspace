"""登录防爆破限流（integration）。

覆盖：
- 连续失败达到账号阈值后返回 429，且提示可读
- 登录成功清零失败计数（打错一次不会影响正常登录）
- 阈值内不误伤
- 不同邮箱共享同一 IP 的失败会累计到 IP 级阈值（撞库场景）
- 限流存储不可用时放行（fail-open，不能让 Redis 抖动阻断登录）
"""

import uuid

import pytest

pytestmark = pytest.mark.integration

LOGIN_URL = "/api/v1/auth/login"


def _unique_email() -> str:
    """每个用例独立邮箱/IP，避免 Redis 计数在用例间串味。"""
    return f"rl-{uuid.uuid4().hex[:12]}@test.com"


async def test_account_lockout_after_threshold(client, make_user):
    email = _unique_email()
    await make_user(email=email, password="RealPass123!")
    ip = {"x-real-ip": "203.0.113.10"}

    max_fails = 5  # settings.login_max_fails_per_account 默认值
    for _ in range(max_fails):
        resp = await client.post(
            LOGIN_URL, json={"email": email, "password": "WrongPass"}, headers=ip
        )
        assert resp.status_code == 403, f"第 {_} 次失败不应被限流"

    # 达到阈值后，即便密码正确也应被拦（锁定期内不给验证机会）
    resp = await client.post(
        LOGIN_URL, json={"email": email, "password": "RealPass123!"}, headers=ip
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "RATE_LIMITED"
    assert "分钟" in resp.json()["error"]["message"]


async def test_success_resets_failure_counter(client, make_user):
    email = _unique_email()
    await make_user(email=email, password="RealPass123!")
    ip = {"x-real-ip": "203.0.113.11"}

    # 打错两次（未到阈值）
    for _ in range(2):
        resp = await client.post(
            LOGIN_URL, json={"email": email, "password": "WrongPass"}, headers=ip
        )
        assert resp.status_code == 403

    # 成功后计数清零
    resp = await client.post(
        LOGIN_URL, json={"email": email, "password": "RealPass123!"}, headers=ip
    )
    assert resp.status_code == 200

    # 再打错 4 次仍不该锁（说明前面的 2 次确实被清掉了，否则 2+4=6 会触发阈值 5）
    for _ in range(4):
        resp = await client.post(
            LOGIN_URL, json={"email": email, "password": "WrongPass"}, headers=ip
        )
        assert resp.status_code == 403, "成功后计数未被清零"


async def test_ip_level_limit_catches_password_spraying(client, make_user):
    """换邮箱喷洒：单账号计数抓不到，必须靠 IP 级累计兜住。"""
    ip_header = {"x-real-ip": "203.0.113.12"}
    # 每个邮箱只失败 3 次（低于账号阈值 5），但总量越过 IP 阈值 30
    for i in range(11):
        email = f"spray-{uuid.uuid4().hex[:10]}@test.com"
        await make_user(email=email, password="RealPass123!")
        for _ in range(3):
            await client.post(
                LOGIN_URL, json={"email": email, "password": "WrongPass"}, headers=ip_header
            )
        if i >= 10:
            break

    resp = await client.post(
        LOGIN_URL,
        json={"email": f"spray-{uuid.uuid4().hex[:10]}@test.com", "password": "x"},
        headers=ip_header,
    )
    assert resp.status_code == 429
    assert "来源" in resp.json()["error"]["message"]


async def test_limiter_check_swallows_redis_error(monkeypatch):
    """check_login_allowed 在 RedisError 时必须静默放行，而不是抛给调用方。

    这是 fail-open 的核心语义：Redis 抖动不能变成「全员无法登录」。
    """
    from redis.exceptions import ConnectionError as RedisConnectionError

    from app.core import login_limiter

    class _Broken:
        async def get(self, *_a, **_kw):
            raise RedisConnectionError("redis down")

        async def aclose(self):
            return None

    monkeypatch.setattr(login_limiter, "_client", lambda: _Broken())
    # 不应抛异常
    await login_limiter.check_login_allowed(email="a@b.com", ip="1.2.3.4")
    # 记录失败也不应抛
    await login_limiter.record_login_failure(email="a@b.com", ip="1.2.3.4")
    await login_limiter.reset_login_failures(email="a@b.com", ip="1.2.3.4")


async def test_malformed_password_not_counted_as_bruteforce(client, make_user):
    """格式错误（422）不该计入爆破次数 —— 用户手滑打错格式不是攻击信号。"""
    email = _unique_email()
    await make_user(email=email, password="RealPass123!")
    ip_header = {"x-real-ip": "203.0.113.13"}

    for _ in range(6):
        resp = await client.post(
            LOGIN_URL, json={"email": "not-an-email", "password": "x"}, headers=ip_header
        )
        assert resp.status_code == 422

    # 换回合法邮箱仍可正常登录（未被 IP 计数误伤到阈值 30）
    resp = await client.post(
        LOGIN_URL, json={"email": email, "password": "RealPass123!"}, headers=ip_header
    )
    assert resp.status_code == 200
