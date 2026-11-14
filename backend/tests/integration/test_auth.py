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


async def test_refresh_token_is_single_use(client, make_user):
    """refresh token 用后立即失效：同一 token 第二次刷新 → 401。"""
    _, password = await make_user(email="rotate@test.com")
    login = await client.post(
        LOGIN_URL, json={"email": "rotate@test.com", "password": password}
    )
    rt = login.json()["tokens"]["refresh"]

    first = await client.post(REFRESH_URL, json={"refresh_token": rt})
    assert first.status_code == 200

    replay = await client.post(REFRESH_URL, json={"refresh_token": rt})
    assert replay.status_code == 401, "旧 refresh token 应已被消费而失效"

    # 轮换出来的新 token 仍可正常刷新
    new_rt = first.json()["tokens"]["refresh"]
    assert new_rt != rt
    third = await client.post(REFRESH_URL, json={"refresh_token": new_rt})
    assert third.status_code == 200


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
