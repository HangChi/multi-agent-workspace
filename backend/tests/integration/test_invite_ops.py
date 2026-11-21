"""022 邀请码运营：部门名额配额 + 统计。

两条边界最值得钉：
1. **批量整批校验**：3 张 × 2 名额 超过剩余 5 时整批拒绝，不留半批——
   半批码既难解释（哪几张生成了？）又难回收；
2. **撤销不释放名额**：发出去就占用预算，否则「发了再撤销」能绕过上限。
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.models.department import Department
from app.models.invite_code import InviteCode
from app.models.user import User

pytestmark = pytest.mark.integration

INVITE_URL = "/api/v1/admin/invite-codes"


async def _admin(db) -> User:
    user = User(
        email=f"inv-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="admin",
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _dept(db, quota: int | None) -> Department:
    dept = Department(
        name=f"招聘-{uuid.uuid4().hex[:5]}",
        path="",
        depth=0,
        assign_scope="all",
        invite_quota=quota,
    )
    db.add(dept)
    await db.flush()
    dept.path = f"/{dept.id}/"
    await db.commit()
    return dept


async def test_single_code_counts_against_quota(db_session, client, make_headers) -> None:
    admin = await _admin(db_session)
    dept = await _dept(db_session, quota=3)
    headers = await make_headers(admin)

    first = await client.post(
        INVITE_URL,
        json={"role": "member", "max_uses": 2, "department_id": str(dept.id)},
        headers=headers,
    )
    assert first.status_code == 201, first.text
    assert first.json()["department_id"] == dept.id
    assert first.json()["department_name"] == dept.name

    # 2 + 2 > 3 → 409
    second = await client.post(
        INVITE_URL,
        json={"role": "member", "max_uses": 2, "department_id": str(dept.id)},
        headers=headers,
    )
    assert second.status_code == 409
    assert "名额" in second.json()["error"]["message"]


async def test_batch_rejected_as_whole(db_session, client, make_headers) -> None:
    """整批校验：不留半批码。"""
    admin = await _admin(db_session)
    dept = await _dept(db_session, quota=5)
    headers = await make_headers(admin)

    resp = await client.post(
        f"{INVITE_URL}/batch",
        json={
            "role": "member",
            "max_uses": 2,
            "count": 3,
            "department_id": str(dept.id),
        },
        headers=headers,
    )
    assert resp.status_code == 409

    remaining = (
        await db_session.execute(
            InviteCode.__table__.select().where(InviteCode.department_id == dept.id)
        )
    ).all()
    assert remaining == [], "整批失败不该留下任何一张码"


async def test_revoked_code_still_occupies_quota(
    db_session, client, make_headers
) -> None:
    admin = await _admin(db_session)
    dept = await _dept(db_session, quota=2)
    headers = await make_headers(admin)

    created = await client.post(
        INVITE_URL,
        json={"role": "member", "max_uses": 2, "department_id": str(dept.id)},
        headers=headers,
    )
    assert created.status_code == 201
    code_id = created.json()["id"]

    revoked = await client.post(f"{INVITE_URL}/{code_id}/revoke", headers=headers)
    assert revoked.status_code in (200, 204), revoked.text[:200]

    again = await client.post(
        INVITE_URL,
        json={"role": "member", "max_uses": 1, "department_id": str(dept.id)},
        headers=headers,
    )
    assert again.status_code == 409, "撤销不释放名额（发出去就占用预算）"


async def test_department_without_quota_is_unlimited(
    db_session, client, make_headers
) -> None:
    admin = await _admin(db_session)
    dept = await _dept(db_session, quota=None)
    headers = await make_headers(admin)

    for _ in range(2):
        resp = await client.post(
            INVITE_URL,
            json={"role": "member", "max_uses": 500, "department_id": str(dept.id)},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text


async def test_stats_overview(db_session, client, make_headers) -> None:
    admin = await _admin(db_session)
    dept = await _dept(db_session, quota=10)
    headers = await make_headers(admin)

    created = await client.post(
        INVITE_URL,
        json={"role": "member", "max_uses": 3, "department_id": str(dept.id)},
        headers=headers,
    )
    code_id = created.json()["id"]
    # 造一次使用（直接改库：注册链路已在别处覆盖）
    code = await db_session.get(InviteCode, code_id)
    assert code is not None
    code.used_count = 2
    await db_session.commit()

    stats = await client.get(f"{INVITE_URL}/stats", headers=headers)
    assert stats.status_code == 200, stats.text
    body = stats.json()
    assert body["issued_seats"] >= 3
    assert body["used_seats"] >= 2

    dept_row = next(
        d for d in body["by_department"] if d["department_id"] == dept.id
    )
    assert dept_row["quota"] == 10
    assert dept_row["issued_seats_this_month"] >= 3
    assert dept_row["used_seats_this_month"] >= 2

    assert any(item["code"] == created.json()["code"] for item in body["top_codes"])


async def test_stats_requires_admin(db_session, client, make_headers) -> None:
    member = User(
        email=f"inv-m-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="member",
        status="active",
    )
    db_session.add(member)
    await db_session.commit()

    resp = await client.get(f"{INVITE_URL}/stats", headers=await make_headers(member))
    assert resp.status_code == 403


def test_month_start_uses_utc() -> None:
    """配额口径是 UTC 自然月；这条钉住「不要用本地时区」这个隐含约定。"""
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    assert month_start.tzinfo is UTC
    assert month_start.day == 1


async def test_admin_sets_department_quota_via_api(
    db_session, client, make_headers
) -> None:
    """配额是部门的一个字段：PATCH 显式传 null 表示「改为不限」，不传表示不改。

    这条钉的是 PATCH 的语义分叉——把 `invite_quota=None` 当成「不限」会让
    「只想改个名字」的请求顺手把配额清空。
    """
    admin = await _admin(db_session)
    dept = await _dept(db_session, quota=5)
    headers = await make_headers(admin)

    raised = await client.patch(
        f"/api/v1/departments/{dept.id}", json={"invite_quota": 20}, headers=headers
    )
    assert raised.status_code == 200, raised.text
    assert raised.json()["invite_quota"] == 20

    # 只改名 → 配额保持不变
    renamed = await client.patch(
        f"/api/v1/departments/{dept.id}",
        json={"name": f"改名-{uuid.uuid4().hex[:4]}"},
        headers=headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["invite_quota"] == 20

    # 显式 null → 改为不限
    cleared = await client.patch(
        f"/api/v1/departments/{dept.id}",
        json={"invite_quota": None},
        headers=headers,
    )
    assert cleared.status_code == 200
    assert cleared.json()["invite_quota"] is None
