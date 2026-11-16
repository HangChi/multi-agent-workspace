"""US1 认证集成测试：注册/登录/刷新/me（T019）。

对齐 contracts/api.md 认证部分：
- POST /auth/register：201（有效邀请码）/ 400（无效/过期/用尽）/ 409（邮箱重复）
- POST /auth/login：200 / 403（密码错误/禁用）
- POST /auth/refresh：200 轮换
- GET /auth/me：200 返回当前用户
"""

import pytest

pytestmark = pytest.mark.integration

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
REFRESH_URL = "/api/v1/auth/refresh"
ME_URL = "/api/v1/auth/me"


async def _make_invite_code(db_session, code="VALID-CODE-123", max_uses=1, used_count=0, expires_at=None):
    from app.models.invite_code import InviteCode

    invite = InviteCode(
        code=code,
        role="member",
        max_uses=max_uses,
        used_count=used_count,
        expires_at=expires_at,
    )
    db_session.add(invite)
    await db_session.commit()
    return invite


# ---------- register ----------


async def test_register_success(client, db_session):
    await _make_invite_code(db_session)
    resp = await client.post(
        REGISTER_URL,
        json={"email": "new@test.com", "password": "StrongPass123!", "invite_code": "VALID-CODE-123"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["user"]["email"] == "new@test.com"
    assert body["user"]["role"] == "member"
    assert body["tokens"]["access"]
    assert body["tokens"]["refresh"]


async def test_one_time_invite_code_cannot_be_used_twice_concurrently(client, db_session):
    """一次性邀请码的消耗必须是原子的：并发注册只能成功一个。"""
    import asyncio

    from sqlalchemy import select

    from app.models.invite_code import InviteCode

    await _make_invite_code(db_session, code="RACE-CODE-1", max_uses=1)
    payloads = [
        {"email": f"race{index}@test.com", "password": "StrongPass123!",
         "invite_code": "RACE-CODE-1"}
        for index in range(2)
    ]
    responses = await asyncio.gather(*(client.post(REGISTER_URL, json=body) for body in payloads))
    codes = sorted(response.status_code for response in responses)
    assert codes == [201, 400], [response.text for response in responses]

    db_session.expire_all()
    invite = (
        await db_session.execute(select(InviteCode).where(InviteCode.code == "RACE-CODE-1"))
    ).scalar_one()
    assert invite.used_count == 1


async def test_register_invalid_invite_code(client, db_session):
    resp = await client.post(
        REGISTER_URL,
        json={"email": "bad@test.com", "password": "StrongPass123!", "invite_code": "NOPE"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_INPUT"


async def test_register_expired_invite_code(client, db_session):
    from datetime import UTC, datetime, timedelta

    await _make_invite_code(db_session, code="EXPIRED-1", expires_at=datetime.now(UTC) - timedelta(hours=1))
    resp = await client.post(
        REGISTER_URL,
        json={"email": "exp@test.com", "password": "StrongPass123!", "invite_code": "EXPIRED-1"},
    )
    assert resp.status_code == 400


async def test_register_used_up_invite_code(client, db_session):
    await _make_invite_code(db_session, code="USED-UP-1", max_uses=1, used_count=1)
    resp = await client.post(
        REGISTER_URL,
        json={"email": "used@test.com", "password": "StrongPass123!", "invite_code": "USED-UP-1"},
    )
    assert resp.status_code == 400


async def test_register_duplicate_email(client, db_session, make_user):
    await make_user(email="dup@test.com")
    await _make_invite_code(db_session, code="DUP-CODE-1")
    resp = await client.post(
        REGISTER_URL,
        json={"email": "dup@test.com", "password": "StrongPass123!", "invite_code": "DUP-CODE-1"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


# ---------- login ----------


async def test_login_success(client, db_session, make_user):
    _, password = await make_user(email="login@test.com")
    resp = await client.post(LOGIN_URL, json={"email": "login@test.com", "password": password})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tokens"]["access"]
    assert body["tokens"]["refresh"]


async def test_login_wrong_password(client, db_session, make_user):
    await make_user(email="wrong@test.com", password="RealPass123!")
    resp = await client.post(LOGIN_URL, json={"email": "wrong@test.com", "password": "BadPass"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_login_disabled_account(client, db_session, make_user):
    _, password = await make_user(email="disabled@test.com", status="disabled")
    resp = await client.post(LOGIN_URL, json={"email": "disabled@test.com", "password": password})
    assert resp.status_code == 403


# ---------- refresh ----------


async def test_refresh_success(client, db_session, make_user):
    _, password = await make_user(email="refresh@test.com")
    login = await client.post(LOGIN_URL, json={"email": "refresh@test.com", "password": password})
    refresh_token = login.json()["tokens"]["refresh"]
    resp = await client.post(REFRESH_URL, json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    assert resp.json()["tokens"]["access"]
    assert resp.json()["tokens"]["refresh"]


async def test_refresh_invalid_token(client):
    resp = await client.post(REFRESH_URL, json={"refresh_token": "not-a-token"})
    assert resp.status_code == 401


# ---------- me ----------


async def test_me_success(client, db_session, make_user, make_headers):
    user, _ = await make_user(email="me@test.com")
    headers = await make_headers(user=user)
    resp = await client.get(ME_URL, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@test.com"
    assert resp.json()["role"] == "member"


async def test_me_unauthorized(client):
    resp = await client.get(ME_URL)
    assert resp.status_code == 401


# ---------- refresh 轮换（一次性消费）----------


async def test_refresh_rotation_issues_new_token(client, make_user):
    """刷新后换发新 token，旧 token 在宽限期后失效（rotation 生效）。"""
    _, password = await make_user(email="rotate@test.com")
    login = await client.post(
        LOGIN_URL, json={"email": "rotate@test.com", "password": password}
    )
    rt = login.json()["tokens"]["refresh"]

    first = await client.post(REFRESH_URL, json={"refresh_token": rt})
    assert first.status_code == 200

    new_rt = first.json()["tokens"]["refresh"]
    assert new_rt != rt, "应换发新的 refresh token"
    assert first.json()["tokens"]["access"]

    # 新 token 可继续刷新（链式）
    second = await client.post(REFRESH_URL, json={"refresh_token": new_rt})
    assert second.status_code == 200


async def test_refresh_replay_within_grace_allowed(client, make_user):
    """宽限期内（默认 30s）同一 token 可再用：多标签页并发刷新不互相踢。

    两个标签页各自刷新时，后到的那个手里还是旧 token；宽限期内两个都能拿到
    新 token 并继续工作。
    """
    _, password = await make_user(email="grace@test.com")
    login = await client.post(LOGIN_URL, json={"email": "grace@test.com", "password": password})
    rt = login.json()["tokens"]["refresh"]

    tab_a = await client.post(REFRESH_URL, json={"refresh_token": rt})
    tab_b = await client.post(REFRESH_URL, json={"refresh_token": rt})
    assert tab_a.status_code == 200
    assert tab_b.status_code == 200, "宽限期内不应互相踢掉"

    # 各自拿到的新 token 都可用
    for resp in (tab_a, tab_b):
        r = await client.post(
            REFRESH_URL, json={"refresh_token": resp.json()["tokens"]["refresh"]}
        )
        assert r.status_code == 200


async def test_refresh_replay_rejected_when_grace_disabled(client, make_user, monkeypatch):
    """refresh_grace_seconds=0 → 严格一次性：第二次立即 401。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "refresh_grace_seconds", 0)

    _, password = await make_user(email="strict@test.com")
    login = await client.post(LOGIN_URL, json={"email": "strict@test.com", "password": password})
    rt = login.json()["tokens"]["refresh"]

    assert (await client.post(REFRESH_URL, json={"refresh_token": rt})).status_code == 200
    replay = await client.post(REFRESH_URL, json={"refresh_token": rt})
    assert replay.status_code == 401


async def test_refresh_replay_rejected_after_grace_window(client, make_user):
    """宽限期用尽（模拟标记过期）后，旧 token 一律 401。"""
    import redis.asyncio as aioredis

    from app.core.config import settings
    from app.core.security import decode_token
    from app.core.token_store import _grace_key

    _, password = await make_user(email="expire@test.com")
    login = await client.post(LOGIN_URL, json={"email": "expire@test.com", "password": password})
    rt = login.json()["tokens"]["refresh"]
    assert (await client.post(REFRESH_URL, json={"refresh_token": rt})).status_code == 200

    # 手动清掉宽限标记，等价于「30 秒已过去」
    client_redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client_redis.delete(_grace_key(decode_token(rt)["jti"]))
    finally:
        await client_redis.aclose()

    replay = await client.post(REFRESH_URL, json={"refresh_token": rt})
    assert replay.status_code == 401


async def test_refresh_token_without_jti_rejected(client, make_user):
    """无 jti 的 token（旧格式/伪造）→ 401，不能绕过轮换校验。"""
    from jose import jwt

    from app.core.config import settings

    user, _ = await make_user(email="nojit@test.com")
    legacy = jwt.encode(
        {
            "user_id": user.id,
            "role": user.role,
            "type": "refresh",
            "exp": 9999999999,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    resp = await client.post(REFRESH_URL, json={"refresh_token": legacy})
    assert resp.status_code == 401


async def test_access_token_not_usable_as_refresh(client, make_user):
    """access token 冒充 refresh → 401（类型校验）。"""
    _, password = await make_user(email="typecast@test.com")
    login = await client.post(
        LOGIN_URL, json={"email": "typecast@test.com", "password": password}
    )
    access = login.json()["tokens"]["access"]
    resp = await client.post(REFRESH_URL, json={"refresh_token": access})
    assert resp.status_code == 401
