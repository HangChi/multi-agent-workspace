"""027 邀请码申请：提交 → 审批 → 发码，以及两条边界。

关键断言：批准走的是 022 的发码路径，所以**部门配额**与「整批不留半批」自动继承
——配额不足时申请必须保持 pending（而不是变成 approved 却没有码）。
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.department import Department
from app.models.invite_code import InviteCode
from app.models.invite_request import InviteRequest
from app.models.notification import Notification
from app.models.user import User

pytestmark = pytest.mark.integration

URL = "/api/v1/invite-requests"


async def _user(db, role: str = "member") -> User:
    user = User(
        email=f"req-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


def _payload(**overrides) -> dict:
    body = {"role": "member", "count": 2, "reason": "新同事入职"}
    body.update(overrides)
    return body


async def test_member_submits_pending_request(db_session, client, make_headers) -> None:
    member = await _user(db_session)
    resp = await client.post(URL, json=_payload(), headers=await make_headers(member))
    assert resp.status_code == 201, resp.text[:300]
    body = resp.json()
    assert body["status"] == "pending"
    assert body["requester_email"] == member.email


async def test_pending_cap_is_three(db_session, client, make_headers) -> None:
    member = await _user(db_session)
    headers = await make_headers(member)
    for _ in range(3):
        assert (
            await client.post(URL, json=_payload(), headers=headers)
        ).status_code == 201
    fourth = await client.post(URL, json=_payload(), headers=headers)
    assert fourth.status_code == 409


async def test_approve_issues_codes_and_notifies(
    db_session, client, make_headers
) -> None:
    admin = await _user(db_session, role="admin")
    member = await _user(db_session)
    created = await client.post(
        URL, json=_payload(count=2), headers=await make_headers(member)
    )
    request_id = created.json()["id"]

    approved = await client.post(
        f"{URL}/{request_id}/approve", headers=await make_headers(admin)
    )
    assert approved.status_code == 200, approved.text[:300]
    body = approved.json()
    assert body["status"] == "approved"
    assert body["issued_codes"] and len(body["issued_codes"].splitlines()) == 2

    # 码真的落库了（不是回显造的）
    codes = (
        (
            await db_session.execute(
                select(InviteCode.code).where(
                    InviteCode.code.in_(body["issued_codes"].splitlines())
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(codes) == 2

    # 申请人收到通知
    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == member.id,
                    Notification.type == "invite_request_approved",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(notes) == 1


async def test_approve_respects_department_quota(
    db_session, client, make_headers
) -> None:
    """配额不足 → 409 且申请保持 pending（不能出现「批了但没码」）。"""
    admin = await _user(db_session, role="admin")
    member = await _user(db_session)
    dept = Department(
        name=f"配额-{uuid.uuid4().hex[:5]}",
        path="",
        depth=0,
        assign_scope="all",
        invite_quota=1,
    )
    db_session.add(dept)
    await db_session.flush()
    dept.path = f"/{dept.id}/"
    await db_session.commit()

    created = await client.post(
        URL,
        json=_payload(count=3, department_id=str(dept.id)),
        headers=await make_headers(member),
    )
    request_id = created.json()["id"]

    denied = await client.post(
        f"{URL}/{request_id}/approve", headers=await make_headers(admin)
    )
    assert denied.status_code == 409

    row = await db_session.get(InviteRequest, request_id)
    assert row is not None and row.status == "pending"
    assert row.issued_codes is None


async def test_reject_and_visibility(db_session, client, make_headers) -> None:
    admin = await _user(db_session, role="admin")
    alice = await _user(db_session)
    bob = await _user(db_session)
    created = await client.post(URL, json=_payload(), headers=await make_headers(alice))
    request_id = created.json()["id"]

    # 成员只看自己的
    bob_list = await client.get(URL, headers=await make_headers(bob))
    assert all(item["requester_id"] == bob.id for item in bob_list.json()["items"])

    # 非 admin 不能审批
    denied = await client.post(
        f"{URL}/{request_id}/approve", headers=await make_headers(bob)
    )
    assert denied.status_code == 403

    rejected = await client.post(
        f"{URL}/{request_id}/reject",
        json={"reason": "本月 HC 已满"},
        headers=await make_headers(admin),
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
