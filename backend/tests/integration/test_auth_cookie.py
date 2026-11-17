"""refresh token 走 HttpOnly cookie 的集成测试。

背景：refresh token 原先前端存 localStorage，一次 XSS 就能拿走 7 天有效的凭证。
改为 HttpOnly cookie 后必须钉住三件事：
1. 登录/注册/刷新都下发 HttpOnly cookie（JS 读不到）；
2. 刷新可以只靠 cookie（不带请求体），并且轮换后会重设新 cookie；
3. 登出撤销后，同一个 refresh 不能再换 token（否则「登出」只是清了个前端变量）。
"""

import pytest

from app.core.config import settings

pytestmark = pytest.mark.integration

LOGIN_URL = "/api/v1/auth/login"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
REGISTER_URL = "/api/v1/auth/register"


def _cookie_header(response) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{settings.refresh_cookie_name}="):
            return header
    raise AssertionError(f"响应没有下发 {settings.refresh_cookie_name} cookie")


async def test_login_sets_httponly_refresh_cookie(client, make_user):
    email = "cookie-login@test.com"
    await make_user(email=email, password="Passw0rd!23")

    response = await client.post(
        LOGIN_URL, json={"email": email, "password": "Passw0rd!23"}
    )

    assert response.status_code == 200
    header = _cookie_header(response)
    assert "HttpOnly" in header
    assert "SameSite=lax" in header.lower() or "samesite=lax" in header.lower()
    assert f"Path={settings.refresh_cookie_path}" in header
    # access token 仍在响应体里（业务接口走 Authorization 头）
    assert response.json()["tokens"]["access"]


async def test_refresh_works_with_cookie_only(client, make_user):
    email = "cookie-refresh@test.com"
    await make_user(email=email, password="Passw0rd!23")
    login = await client.post(
        LOGIN_URL, json={"email": email, "password": "Passw0rd!23"}
    )
    assert login.status_code == 200

    # 不带请求体：完全依赖 TestClient 保存的 cookie jar
    response = await client.post(REFRESH_URL)

    assert response.status_code == 200
    assert response.json()["tokens"]["access"]
    # 轮换后必须重设 cookie，否则浏览器拿着已消费的旧票，下次刷新就掉线
    assert settings.refresh_cookie_name in response.headers.get("set-cookie", "")


async def test_refresh_without_any_token_is_401(client):
    response = await client.post(REFRESH_URL)
    assert response.status_code == 401


async def test_logout_revokes_refresh_and_clears_cookie(client, make_user):
    email = "cookie-logout@test.com"
    await make_user(email=email, password="Passw0rd!23")
    login = await client.post(
        LOGIN_URL, json={"email": email, "password": "Passw0rd!23"}
    )
    refresh_cookie = _cookie_header(login).split(";")[0]  # aw_refresh=<jwt>

    logout = await client.post(LOGOUT_URL)
    assert logout.status_code == 200
    assert "Max-Age=0" in logout.headers.get("set-cookie", "") or (
        "expires" in logout.headers.get("set-cookie", "").lower()
    )

    # 用「登出前保存的」cookie 再刷新：必须失败（服务端已撤销）
    response = await client.post(REFRESH_URL, headers={"Cookie": refresh_cookie})
    assert response.status_code == 401


async def test_logout_is_idempotent_without_cookie(client):
    assert (await client.post(LOGOUT_URL)).status_code == 200


async def test_register_sets_cookie_too(client, db_session):
    from app.models.invite_code import InviteCode

    db_session.add(
        InviteCode(code="COOKIE-REG-1", role="member", max_uses=1, used_count=0)
    )
    await db_session.commit()

    response = await client.post(
        REGISTER_URL,
        json={
            "email": "cookie-register@test.com",
            "password": "Passw0rd!23",
            "invite_code": "COOKIE-REG-1",
        },
    )

    assert response.status_code == 201
    assert "HttpOnly" in _cookie_header(response)
