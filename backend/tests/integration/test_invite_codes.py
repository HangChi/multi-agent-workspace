"""邀请码管理端点的集成测试（015）。

覆盖：
- admin 发放 → 码可用 → 注册成功（role 跟随码）
- 列表能看到刚发的码与状态
- member 无权管理（403）
- 吊销后注册被拒（400 无效）、重复吊销 409、不存在 404
- 状态判定：active / exhausted / expired / revoked
"""

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

CODES_URL = "/api/v1/admin/invite-codes"
REGISTER_URL = "/api/v1/auth/register"


async def _create_code(client, headers, **payload):
    return await client.post(CODES_URL, json=payload or {}, headers=headers)


async def test_admin_creates_and_lists_code(client, make_headers):
    headers = await make_headers(role="admin")

    created = await _create_code(client, headers, role="member", max_uses=3)
    assert created.status_code == 201
    body = created.json()
    assert len(body["code"]) == 12
    assert body["state"] == "active"
    assert body["used_count"] == 0
    assert body["max_uses"] == 3

    listed = await client.get(CODES_URL, headers=headers)
    assert listed.status_code == 200
    data = listed.json()
    assert data["total"] >= 1
    assert any(item["code"] == body["code"] for item in data["items"])


async def test_created_code_can_register(client, make_headers):
    headers = await make_headers(role="admin")
    code = (await _create_code(client, headers, max_uses=2)).json()["code"]

    resp = await client.post(
        REGISTER_URL,
        json={
            "email": "invited@test.com",
            "password": "StrongPass123!",
            "invite_code": code,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["role"] == "member"

    # 用掉一次：state 仍是 active（上限 2）
    listed = await client.get(CODES_URL, headers=headers)
    row = next(i for i in listed.json()["items"] if i["code"] == code)
    assert row["used_count"] == 1
    assert row["state"] == "active"


async def test_viewer_code_yields_viewer(client, make_headers):
    headers = await make_headers(role="admin")
    code = (await _create_code(client, headers, role="viewer")).json()["code"]

    resp = await client.post(
        REGISTER_URL,
        json={
            "email": "viewer-invited@test.com",
            "password": "StrongPass123!",
            "invite_code": code,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["role"] == "viewer"


async def test_member_cannot_manage_codes(client, make_headers):
    headers = await make_headers(role="member")
    assert (await client.get(CODES_URL, headers=headers)).status_code == 403
    assert (await _create_code(client, headers)).status_code == 403


async def test_revoke_blocks_registration(client, make_headers):
    headers = await make_headers(role="admin")
    code_row = (await _create_code(client, headers, max_uses=5)).json()

    revoked = await client.post(f"{CODES_URL}/{code_row['id']}/revoke", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["state"] == "revoked"
    assert revoked.json()["revoked_at"] is not None

    resp = await client.post(
        REGISTER_URL,
        json={
            "email": "after-revoke@test.com",
            "password": "StrongPass123!",
            "invite_code": code_row["code"],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "邀请码无效"


async def test_revoke_twice_is_conflict(client, make_headers):
    headers = await make_headers(role="admin")
    code_row = (await _create_code(client, headers)).json()
    first = await client.post(f"{CODES_URL}/{code_row['id']}/revoke", headers=headers)
    assert first.status_code == 200
    second = await client.post(f"{CODES_URL}/{code_row['id']}/revoke", headers=headers)
    assert second.status_code == 409


async def test_revoke_missing_is_404(client, make_headers):
    headers = await make_headers(role="admin")
    resp = await client.post(
        f"{CODES_URL}/00000000-0000-0000-0000-000000000000/revoke", headers=headers
    )
    assert resp.status_code == 404


async def test_state_exhausted_after_last_use(client, make_headers):
    headers = await make_headers(role="admin")
    code = (await _create_code(client, headers, max_uses=1)).json()["code"]

    ok = await client.post(
        REGISTER_URL,
        json={
            "email": "single-use@test.com",
            "password": "StrongPass123!",
            "invite_code": code,
        },
    )
    assert ok.status_code == 201

    listed = await client.get(CODES_URL, headers=headers)
    row = next(i for i in listed.json()["items"] if i["code"] == code)
    assert row["state"] == "exhausted"


async def test_state_expired(client, make_headers, db_session):
    from app.models.invite_code import InviteCode

    headers = await make_headers(role="admin")
    stale = InviteCode(
        code="EXPIRED-CODE-001",
        role="member",
        max_uses=5,
        used_count=0,
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    db_session.add(stale)
    # 必须 commit：API 走独立 session，未提交的行它看不见
    await db_session.commit()

    listed = await client.get(CODES_URL, headers=headers)
    row = next(i for i in listed.json()["items"] if i["code"] == "EXPIRED-CODE-001")
    assert row["state"] == "expired"

    resp = await client.post(
        REGISTER_URL,
        json={
            "email": "expired@test.com",
            "password": "StrongPass123!",
            "invite_code": "EXPIRED-CODE-001",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "邀请码已过期"


async def test_batch_create_returns_distinct_active_codes(client, make_headers):
    headers = await make_headers(role="admin")
    resp = await client.post(
        f"{CODES_URL}/batch",
        json={"role": "member", "max_uses": 2, "count": 3},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["total"] == 3
    codes = [item["code"] for item in body["items"]]
    assert len(set(codes)) == 3  # 不重复
    assert all(item["state"] == "active" for item in body["items"])


async def test_batch_create_rejects_oversized_count(client, make_headers):
    headers = await make_headers(role="admin")
    resp = await client.post(
        f"{CODES_URL}/batch", json={"count": 21}, headers=headers
    )
    assert resp.status_code == 422


async def test_batch_create_member_forbidden(client, make_headers):
    resp = await client.post(
        f"{CODES_URL}/batch", json={"count": 2}, headers=await make_headers(role="member")
    )
    assert resp.status_code == 403
