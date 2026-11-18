"""010 成员管理端点集成测试（T006，M-1~M-12）：CRUD + 三级谓词 + 通知。

三级谓词（contracts §1.2）：owner 全权 / 成员可读列表 / 陌生人 404。
错误码分工：成员做 owner 动作 = 403（他知道项目存在，不够权）；
陌生人碰项目一切 = 404（不暴露存在性）——两种「不够权」不混码。
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.project import Project
from app.models.user import User

pytestmark = pytest.mark.integration

MEMBERS_URL = "/api/v1/projects/{pid}/members"
PROJECTS_URL = "/api/v1/projects"


async def _user(db, role: str = "member", status: str = "active") -> User:
    user = User(
        email=f"mem-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status=status,
    )
    db.add(user)
    await db.commit()
    return user


async def _project(db, owner: User) -> Project:
    project = Project(owner_id=owner.id, name=f"项目-{uuid.uuid4().hex[:6]}")
    db.add(project)
    await db.commit()
    return project


# ---------- M-1/M-2/M-3/M-4：加的四种结果 ----------


async def test_add_member_201_with_notification(db_session, client, make_headers):
    """M-1：加成员 201；被加者收到 project_member_added（只发他本人）。"""
    owner = await _user(db_session)
    colleague = await _user(db_session)
    project = await _project(db_session, owner)

    resp = await client.post(
        MEMBERS_URL.format(pid=project.id),
        json={"user_id": colleague.id},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["user_id"] == colleague.id
    assert body["is_owner"] is False

    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == colleague.id,
                    Notification.type == "project_member_added",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(notes) == 1
    assert notes[0].payload["project_name"] == project.name
    # owner 自己不该收到「你被加入了」（只发给对方本人）
    owner_notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == owner.id,
                    Notification.type == "project_member_added",
                )
            )
        )
        .scalars()
        .all()
    )
    assert owner_notes == []


async def test_duplicate_member_409(db_session, client, make_headers):
    """M-2：重复加 → 409。"""
    owner = await _user(db_session)
    colleague = await _user(db_session)
    project = await _project(db_session, owner)
    headers = await make_headers(owner)
    first = await client.post(
        MEMBERS_URL.format(pid=project.id),
        json={"user_id": colleague.id},
        headers=headers,
    )
    assert first.status_code == 201
    second = await client.post(
        MEMBERS_URL.format(pid=project.id),
        json={"user_id": colleague.id},
        headers=headers,
    )
    assert second.status_code == 409


async def test_inactive_user_400(db_session, client, make_headers):
    """M-3：停用用户不能被加（在职口径，同分派选人，R-08）。"""
    owner = await _user(db_session)
    retired = await _user(db_session, status="disabled")
    project = await _project(db_session, owner)
    resp = await client.post(
        MEMBERS_URL.format(pid=project.id),
        json={"user_id": retired.id},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_INPUT"


async def test_missing_user_404(db_session, client, make_headers):
    """M-4：目标不存在 → 404。"""
    owner = await _user(db_session)
    project = await _project(db_session, owner)
    resp = await client.post(
        MEMBERS_URL.format(pid=project.id),
        json={"user_id": str(uuid.uuid4())},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 404


# ---------- M-5/M-6/M-11：三级谓词 ----------


async def test_member_cannot_manage_but_can_list(db_session, client, make_headers):
    """M-5/M-10：成员加人 403（不够权但知道项目存在）；列表 200。"""
    owner = await _user(db_session)
    colleague = await _user(db_session)
    third = await _user(db_session)
    project = await _project(db_session, owner)
    await client.post(
        MEMBERS_URL.format(pid=project.id),
        json={"user_id": colleague.id},
        headers=await make_headers(owner),
    )

    add = await client.post(
        MEMBERS_URL.format(pid=project.id),
        json={"user_id": third.id},
        headers=await make_headers(colleague),
    )
    assert add.status_code == 403

    listing = await client.get(
        MEMBERS_URL.format(pid=project.id), headers=await make_headers(colleague)
    )
    assert listing.status_code == 200


async def test_stranger_404_on_member_ops(db_session, client, make_headers):
    """M-6/M-11：陌生人碰成员端点一律 404（不暴露存在性）。"""
    owner = await _user(db_session)
    stranger = await _user(db_session)
    third = await _user(db_session)
    project = await _project(db_session, owner)
    headers = await make_headers(stranger)

    add = await client.post(
        MEMBERS_URL.format(pid=project.id),
        json={"user_id": third.id},
        headers=headers,
    )
    assert add.status_code == 404
    listing = await client.get(MEMBERS_URL.format(pid=project.id), headers=headers)
    assert listing.status_code == 404


# ---------- M-7/M-8：移除的守卫与 404 ----------


async def test_owner_cannot_remove_self(db_session, client, make_headers):
    """M-7/R-04：owner 自移除 → 409（无主项目是脏状态）。"""
    owner = await _user(db_session)
    project = await _project(db_session, owner)
    resp = await client.delete(
        f"{MEMBERS_URL.format(pid=project.id)}/{owner.id}",
        headers=await make_headers(owner),
    )
    assert resp.status_code == 409
    assert "退出" in resp.json()["error"]["message"]


async def test_remove_member_notifies_and_removes(db_session, client, make_headers):
    """M-8 延伸 + US4：移除 204；被移出者收到 removed 通知。"""
    owner = await _user(db_session)
    colleague = await _user(db_session)
    project = await _project(db_session, owner)
    await client.post(
        MEMBERS_URL.format(pid=project.id),
        json={"user_id": colleague.id},
        headers=await make_headers(owner),
    )
    resp = await client.delete(
        f"{MEMBERS_URL.format(pid=project.id)}/{colleague.id}",
        headers=await make_headers(owner),
    )
    assert resp.status_code == 204
    listing = await client.get(
        MEMBERS_URL.format(pid=project.id), headers=await make_headers(owner)
    )
    assert all(m["user_id"] != colleague.id for m in listing.json()["items"])

    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == colleague.id,
                    Notification.type == "project_member_removed",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(notes) == 1


async def test_remove_missing_relation_404(db_session, client, make_headers):
    """M-8：移除不存在的关系 → 404。"""
    owner = await _user(db_session)
    stranger = await _user(db_session)
    project = await _project(db_session, owner)
    resp = await client.delete(
        f"{MEMBERS_URL.format(pid=project.id)}/{stranger.id}",
        headers=await make_headers(owner),
    )
    assert resp.status_code == 404


# ---------- M-9：列表含 owner 标记 ----------


async def test_list_marks_owner(db_session, client, make_headers):
    """M-9：成员列表含 owner（is_owner=true）+ 成员（is_owner=false）。"""
    owner = await _user(db_session)
    colleague = await _user(db_session)
    project = await _project(db_session, owner)
    await client.post(
        MEMBERS_URL.format(pid=project.id),
        json={"user_id": colleague.id},
        headers=await make_headers(owner),
    )
    resp = await client.get(
        MEMBERS_URL.format(pid=project.id), headers=await make_headers(owner)
    )
    items = resp.json()["items"]
    owners = [m for m in items if m["is_owner"]]
    assert len(owners) == 1
    assert owners[0]["user_id"] == owner.id
    assert any(m["user_id"] == colleague.id and not m["is_owner"] for m in items)


# ---------- M-12：删项目级联 + 无通知 ----------


async def test_delete_project_cascades_members_without_notice(
    db_session, client, make_headers
):
    """M-12/R-05：删项目 → 成员行级联消失；**不发** removed 通知
    （点进 404 的通知是噪声）。"""
    owner = await _user(db_session)
    colleague = await _user(db_session)
    project = await _project(db_session, owner)
    await client.post(
        MEMBERS_URL.format(pid=project.id),
        json={"user_id": colleague.id},
        headers=await make_headers(owner),
    )
    resp = await client.delete(
        f"{PROJECTS_URL}/{project.id}", headers=await make_headers(owner)
    )
    assert resp.status_code == 204

    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == colleague.id,
                    Notification.type == "project_member_removed",
                )
            )
        )
        .scalars()
        .all()
    )
    assert notes == []
    listing = await client.get(
        MEMBERS_URL.format(pid=project.id), headers=await make_headers(owner)
    )
    assert listing.status_code == 404  # 项目没了


# ---------- 审计（FR-010） ----------


async def test_member_ops_write_audit(db_session, client, make_headers):
    """FR-010：增删成员各落一条审计（按 user_id 过滤——共享库教训）。"""
    owner = await _user(db_session)
    colleague = await _user(db_session)
    project = await _project(db_session, owner)
    headers = await make_headers(owner)
    await client.post(
        MEMBERS_URL.format(pid=project.id),
        json={"user_id": colleague.id},
        headers=headers,
    )
    await client.delete(
        f"{MEMBERS_URL.format(pid=project.id)}/{colleague.id}", headers=headers
    )

    rows = (
        await db_session.execute(
            select(AuditLog.action, AuditLog.detail).where(
                AuditLog.action.in_(["project_member_add", "project_member_remove"]),
                AuditLog.user_id == owner.id,
            )
        )
    ).all()
    actions = {action for action, _ in rows}
    assert actions == {"project_member_add", "project_member_remove"}
    detail = next(d for action, d in rows if action == "project_member_add")
    assert detail["member_user_id"] == colleague.id
    assert detail["project_name"] == project.name
