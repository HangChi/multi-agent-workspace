"""017 所有权移交：HTTP 层分支 + 事务副作用 + 回滚不变式。

契约见 specs/017-project-ownership-transfer/contracts/api.md §2：
错误矩阵的**顺序本身**是契约的一部分（陌生人不能靠「传一个不存在的用户」
探测项目是否存在）。所以这里按序逐条钉住，而不是只测「最后回什么码」。
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User

pytestmark = pytest.mark.integration

PROJECTS_URL = "/api/v1/projects"
TRANSFER_URL = "/api/v1/projects/{pid}/transfer"
MEMBERS_URL = "/api/v1/projects/{pid}/members"


async def _user(db, role: str = "member", status: str = "active") -> User:
    user = User(
        email=f"tr-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status=status,
    )
    db.add(user)
    await db.commit()
    return user


async def _project(db, owner: User, name: str | None = None) -> Project:
    project = Project(owner_id=owner.id, name=name or f"项目-{uuid.uuid4().hex[:6]}")
    db.add(project)
    await db.commit()
    return project


async def _add_member(db, project: Project, member: User) -> None:
    db.add(ProjectMember(project_id=project.id, user_id=member.id))
    await db.commit()


# ---------------------------------------------------------------- 成功路径


async def test_transfer_keeps_old_owner_as_member(
    db_session, client, make_headers
) -> None:
    """US1：归属变更 + 新 owner 出成员表 + 原 owner 进成员表 + 通知 + 审计。"""
    owner = await _user(db_session)
    successor = await _user(db_session)
    project = await _project(db_session, owner)
    await _add_member(db_session, project, successor)

    resp = await client.post(
        TRANSFER_URL.format(pid=project.id),
        json={"new_owner_id": successor.id, "keep_as_member": True},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["owner_id"] == successor.id
    # member_count 的口径是「含 owner」（010 FR-009）：新 owner + 保留的原 owner = 2
    assert body["member_count"] == 2

    await db_session.refresh(project)
    assert project.owner_id == successor.id

    member_ids = {
        row
        for (row,) in (
            await db_session.execute(
                select(ProjectMember.user_id).where(
                    ProjectMember.project_id == project.id
                )
            )
        ).all()
    }
    assert member_ids == {owner.id}, "新 owner 的成员行必须被删除，原 owner 必须在"

    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == successor.id,
                    Notification.type == "project_ownership_received",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(notes) == 1
    assert notes[0].payload["previous_owner_id"] == owner.id
    assert notes[0].payload["project_name"] == project.name

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "project_transfer",
                    AuditLog.target_id == project.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].detail["from_user_id"] == owner.id
    assert audits[0].detail["to_user_id"] == successor.id
    assert audits[0].detail["keep_as_member"] is True


async def test_transfer_without_keeping_member_loses_access(
    db_session, client, make_headers
) -> None:
    """US2：keep_as_member=false → 原 owner 访问 404、两个 scope 都看不到。"""
    owner = await _user(db_session)
    successor = await _user(db_session)
    project = await _project(db_session, owner)
    await _add_member(db_session, project, successor)

    resp = await client.post(
        TRANSFER_URL.format(pid=project.id),
        json={"new_owner_id": successor.id, "keep_as_member": False},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 200, resp.text

    # 原 owner 不再是参与者：详情 404（016：不暴露存在性）
    detail = await client.get(
        f"{PROJECTS_URL}/{project.id}", headers=await make_headers(owner)
    )
    assert detail.status_code == 404

    for scope in ("mine", "relevant"):
        listed = await client.get(
            f"{PROJECTS_URL}?scope={scope}", headers=await make_headers(owner)
        )
        assert listed.status_code == 200
        assert all(item["id"] != project.id for item in listed.json()["items"])

    # 成功方（新 owner）能看到
    mine = await client.get(
        f"{PROJECTS_URL}?scope=mine", headers=await make_headers(successor)
    )
    assert any(item["id"] == project.id for item in mine.json()["items"])


async def test_new_owner_gets_owner_powers(db_session, client, make_headers) -> None:
    """US3：新 owner 四个动作可用，原 owner 只剩读（403 不是 404）。"""
    owner = await _user(db_session)
    successor = await _user(db_session)
    colleague = await _user(db_session)
    project = await _project(db_session, owner)
    await _add_member(db_session, project, successor)

    await client.post(
        TRANSFER_URL.format(pid=project.id),
        json={"new_owner_id": successor.id},
        headers=await make_headers(owner),
    )

    renamed = await client.patch(
        f"{PROJECTS_URL}/{project.id}",
        json={"name": f"改名-{uuid.uuid4().hex[:4]}"},
        headers=await make_headers(successor),
    )
    assert renamed.status_code == 200

    added = await client.post(
        MEMBERS_URL.format(pid=project.id),
        json={"user_id": colleague.id},
        headers=await make_headers(successor),
    )
    assert added.status_code == 201

    # 原 owner 现在是成员：owner 动作 403（他知道项目存在）
    denied = await client.patch(
        f"{PROJECTS_URL}/{project.id}",
        json={"name": "x"},
        headers=await make_headers(owner),
    )
    assert denied.status_code == 403


# ---------------------------------------------------------------- 六步校验


async def test_transfer_to_self_409(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    project = await _project(db_session, owner)

    resp = await client.post(
        TRANSFER_URL.format(pid=project.id),
        json={"new_owner_id": owner.id},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 409
    assert "自己" in resp.json()["error"]["message"]


async def test_target_not_found_404(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    project = await _project(db_session, owner)

    resp = await client.post(
        TRANSFER_URL.format(pid=project.id),
        json={"new_owner_id": str(uuid.uuid4())},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["message"] == "用户不存在"


async def test_target_inactive_400(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    inactive = await _user(db_session, status="disabled")
    project = await _project(db_session, owner)
    await _add_member(db_session, project, inactive)

    resp = await client.post(
        TRANSFER_URL.format(pid=project.id),
        json={"new_owner_id": inactive.id},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 400
    assert "停用" in resp.json()["error"]["message"]


async def test_target_viewer_409(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    viewer = await _user(db_session, role="viewer")
    project = await _project(db_session, owner)
    await _add_member(db_session, project, viewer)

    resp = await client.post(
        TRANSFER_URL.format(pid=project.id),
        json={"new_owner_id": viewer.id},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 409
    assert "只读" in resp.json()["error"]["message"]


async def test_target_not_member_409(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    outsider = await _user(db_session)
    project = await _project(db_session, owner)

    resp = await client.post(
        TRANSFER_URL.format(pid=project.id),
        json={"new_owner_id": outsider.id},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 409
    assert "不是项目成员" in resp.json()["error"]["message"]


async def test_same_name_conflict_rolls_back(db_session, client, make_headers) -> None:
    """US4：同名 409，且归属**不变**（事务整体回滚）。"""
    owner = await _user(db_session)
    successor = await _user(db_session)
    project = await _project(db_session, owner, name="官网改版")
    await _add_member(db_session, project, successor)
    # 接手人名下已有同名项目
    await _project(db_session, successor, name="官网改版")

    resp = await client.post(
        TRANSFER_URL.format(pid=project.id),
        json={"new_owner_id": successor.id, "keep_as_member": True},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 409
    message = resp.json()["error"]["message"]
    assert "官网改版" in message, "冲突消息必须带项目名，否则用户不知道怎么修"

    await db_session.refresh(project)
    assert project.owner_id == owner.id, "冲突后归属必须保持原样"
    # 原 owner 的成员行不能被写进去（那是移交成功的副作用）
    assert not any(
        row == owner.id
        for (row,) in (
            await db_session.execute(
                select(ProjectMember.user_id).where(
                    ProjectMember.project_id == project.id
                )
            )
        ).all()
    )
    assert not (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == successor.id,
                    Notification.type == "project_ownership_received",
                )
            )
        )
        .scalars()
        .all()
    ), "失败路径不能留下通知"


# ---------------------------------------------------------------- 调用者权限


async def test_stranger_404(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    stranger = await _user(db_session)
    project = await _project(db_session, owner)

    resp = await client.post(
        TRANSFER_URL.format(pid=project.id),
        json={"new_owner_id": stranger.id},
        headers=await make_headers(stranger),
    )
    assert resp.status_code == 404
    # 与「项目不存在」逐字相同（016）
    assert resp.json()["error"]["message"] == "项目不存在"


async def test_member_caller_403(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    member = await _user(db_session)
    project = await _project(db_session, owner)
    await _add_member(db_session, project, member)

    resp = await client.post(
        TRANSFER_URL.format(pid=project.id),
        json={"new_owner_id": member.id},
        headers=await make_headers(member),
    )
    assert resp.status_code == 403
    assert "创建者" in resp.json()["error"]["message"]


async def test_viewer_caller_403(db_session, client, make_headers) -> None:
    """viewer 连端点都不该进（MemberUser 依赖先拦）。"""
    owner = await _user(db_session)
    viewer = await _user(db_session, role="viewer")
    project = await _project(db_session, owner)

    resp = await client.post(
        TRANSFER_URL.format(pid=project.id),
        json={"new_owner_id": viewer.id},
        headers=await make_headers(viewer),
    )
    assert resp.status_code == 403


async def test_unauthenticated_401(client) -> None:
    resp = await client.post(
        TRANSFER_URL.format(pid=str(uuid.uuid4())),
        json={"new_owner_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------- 关联行为


async def test_owner_self_removal_points_to_transfer(
    db_session, client, make_headers
) -> None:
    """FR-009：owner 自移除的 409 文案要指向「移交」而不是只提删项目。"""
    owner = await _user(db_session)
    project = await _project(db_session, owner)

    resp = await client.delete(
        f"{MEMBERS_URL.format(pid=project.id)}/{owner.id}",
        headers=await make_headers(owner),
    )
    assert resp.status_code == 409
    message = resp.json()["error"]["message"]
    assert "移交" in message
    assert "删除项目" in message


async def test_requests_keep_project_after_transfer(
    db_session, client, make_headers
) -> None:
    """008 快照纪律：移交不改需求归属，也不改写 project_name 快照。"""
    from app.models.request import Request

    owner = await _user(db_session)
    successor = await _user(db_session)
    project = await _project(db_session, owner, name="不改名项目")
    await _add_member(db_session, project, successor)
    request = Request(
        title="留在项目里的需求",
        owner_id=owner.id,
        project_id=project.id,
        project_name=project.name,
    )
    db_session.add(request)
    await db_session.commit()

    resp = await client.post(
        TRANSFER_URL.format(pid=project.id),
        json={"new_owner_id": successor.id},
        headers=await make_headers(owner),
    )
    assert resp.status_code == 200

    await db_session.refresh(request)
    assert request.project_id == project.id
    assert request.project_name == "不改名项目"
