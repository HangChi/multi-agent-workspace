"""冒烟测试：验证测试基座（client / db_session / make_user / make_headers）可用。"""

import pytest

pytestmark = pytest.mark.integration


async def test_health(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_make_user_and_headers(make_user, make_headers):
    user, _ = await make_user(email="smoke@test.com")
    assert user.email == "smoke@test.com"
    assert user.status == "active"

    headers = await make_headers(user=user)
    assert headers["Authorization"].startswith("Bearer ")


async def test_unknown_route_returns_404(client):
    resp = await client.get("/api/v1/not-exist")
    assert resp.status_code == 404
