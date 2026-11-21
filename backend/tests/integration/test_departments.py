"""020 组织架构与分派范围：集成测试。

两类断言：
1. **树与路径**：物化路径 / subtree 查询 / 删部门时子树与人员归属的处理；
2. **分派门禁**：策略为 subtree/self 时「谁派得到谁」，以及默认（无部门）不收紧。
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.department import Department
from app.models.user import User
from app.services import org_service

pytestmark = pytest.mark.integration

DEPTS_URL = "/api/v1/departments"
REQUESTS_URL = "/api/v1/requests"


async def _user(db, role: str = "member") -> User:
    user = User(
        email=f"org-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _dept(db, name: str, parent: Department | None = None, scope: str = "all"):
    # 名字必须唯一：这些用例会 commit（不是靠 rollback 隔离），
    # 固定名字会在第二次跑或后续用例里撞上「同父重名」。
    unique = f"{name}-{uuid.uuid4().hex[:5]}"
    dept = await org_service.create_department(
        db, name=unique, parent_id=parent.id if parent else None, assign_scope=scope
    )
    await db.commit()
    return dept


# ---------------------------------------------------------------- 树与路径


async def test_tree_path_and_depth(db_session) -> None:
    root = await _dept(db_session, "研发中心")
    child = await _dept(db_session, "平台组", parent=root)
    grand = await _dept(db_session, "基础设施", parent=child)

    assert root.path == f"/{root.id}/"
    assert child.path == f"{root.path}{child.id}/"
    assert grand.path == f"{child.path}{grand.id}/"
    assert (root.depth, child.depth, grand.depth) == (0, 1, 2)


async def test_duplicate_name_only_within_same_parent(db_session) -> None:
    from app.api.errors import AppError

    root = await _dept(db_session, "总部")
    tag = uuid.uuid4().hex[:5]
    name = f"平台组-{tag}"
    a = await org_service.create_department(
        db_session, name=name, parent_id=root.id, assign_scope="all"
    )
    # 先取出 id：下面的 rollback 会让 a 的属性过期，之后再访问会触发异步 IO
    a_id = a.id
    await db_session.commit()

    # 同一父下重名 → 409 CONFLICT
    with pytest.raises(AppError) as exc:
        await org_service.create_department(db_session, name=name, parent_id=root.id)
    assert exc.value.code == "CONFLICT"
    await db_session.rollback()

    # 不同父下同名 → 允许（两个部门都叫「平台组」是现实）
    other = await _dept(db_session, "子公司")
    b = await org_service.create_department(
        db_session, name=name, parent_id=other.id, assign_scope="all"
    )
    await db_session.commit()
    assert a_id != b.id


async def test_delete_department_clears_members(db_session) -> None:
    root = await _dept(db_session, "待删除")
    child = await _dept(db_session, "子部门", parent=root)
    alice = await _user(db_session)
    bob = await _user(db_session)
    await org_service.set_user_department(
        db_session, user_id=alice.id, department_id=root.id
    )
    await org_service.set_user_department(
        db_session, user_id=bob.id, department_id=child.id
    )
    await db_session.commit()

    await org_service.delete_department(db_session, department_id=root.id)
    await db_session.commit()

    remaining = (
        (
            await db_session.execute(
                select(Department).where(Department.path.like(f"/{root.id}%"))
            )
        )
        .scalars()
        .all()
    )
    assert remaining == []
    for user_id in (alice.id, bob.id):
        user = await db_session.get(User, user_id)
        assert user is not None and user.department_id is None


# ---------------------------------------------------------------- 分派范围


async def test_scope_subtree_allows_descendants(db_session) -> None:
    root = await _dept(db_session, "研发", scope="subtree")
    child = await _dept(db_session, "平台组", parent=root, scope="all")
    grand = await _dept(db_session, "基础设施", parent=child)

    actor = await _user(db_session)
    await org_service.set_user_department(
        db_session, user_id=actor.id, department_id=root.id
    )
    await db_session.commit()

    allowed = await org_service.allowed_target_department_ids(db_session, actor.id)
    assert allowed == {root.id, child.id, grand.id}


async def test_scope_self_only_own_department(db_session) -> None:
    root = await _dept(db_session, "研发", scope="self")
    await _dept(db_session, "平台组", parent=root)  # 子部门不该出现在 self 的范围里
    actor = await _user(db_session)
    await org_service.set_user_department(
        db_session, user_id=actor.id, department_id=root.id
    )
    await db_session.commit()

    assert await org_service.allowed_target_department_ids(db_session, actor.id) == {
        root.id
    }


async def test_scope_all_and_no_department_are_unrestricted(db_session) -> None:
    dept = await _dept(db_session, "全公司", scope="all")
    inside = await _user(db_session)
    await org_service.set_user_department(
        db_session, user_id=inside.id, department_id=dept.id
    )
    outsider = await _user(db_session)
    await db_session.commit()

    # 策略 all → 不限
    assert (
        await org_service.allowed_target_department_ids(db_session, inside.id) is None
    )
    # 没有部门 → 不限（升级后行为零变化）
    assert (
        await org_service.allowed_target_department_ids(db_session, outsider.id) is None
    )


async def test_can_assign_to_respects_scope(db_session) -> None:
    dept = await _dept(db_session, "受限部门", scope="self")
    other_dept = await _dept(db_session, "外部部门")
    actor = await _user(db_session)
    peer = await _user(db_session)
    colleague = await _user(db_session)
    await org_service.set_user_department(
        db_session, user_id=actor.id, department_id=dept.id
    )
    await org_service.set_user_department(
        db_session, user_id=peer.id, department_id=dept.id
    )
    await org_service.set_user_department(
        db_session, user_id=colleague.id, department_id=other_dept.id
    )
    await db_session.commit()

    assert await org_service.can_assign_to(db_session, actor.id, peer.id) is True
    assert await org_service.can_assign_to(db_session, actor.id, colleague.id) is False
    # 目标没部门且自己受限 → 不放行（未归档的人不在任何范围内）
    loner = await _user(db_session)
    assert await org_service.can_assign_to(db_session, actor.id, loner.id) is False
    # 派给自己永远可以
    assert await org_service.can_assign_to(db_session, actor.id, actor.id) is True


async def test_allowed_user_ids_filters_options(db_session) -> None:
    dept = await _dept(db_session, "只看自己", scope="self")
    actor = await _user(db_session)
    peer = await _user(db_session)
    outsider = await _user(db_session)
    await org_service.set_user_department(
        db_session, user_id=actor.id, department_id=dept.id
    )
    await org_service.set_user_department(
        db_session, user_id=peer.id, department_id=dept.id
    )
    await db_session.commit()

    ids = await org_service.allowed_user_ids(db_session, actor.id)
    assert ids is not None
    assert peer.id in ids and actor.id in ids
    assert outsider.id not in ids


# ---------------------------------------------------------------- HTTP 层


async def test_non_admin_cannot_create_department(
    db_session, client, make_headers
) -> None:
    member = await _user(db_session)
    resp = await client.post(
        DEPTS_URL,
        json={"name": f"部门-{uuid.uuid4().hex[:5]}"},
        headers=await make_headers(member),
    )
    assert resp.status_code == 403


async def test_admin_creates_and_lists_with_member_count(
    db_session, client, make_headers
) -> None:
    admin = await _user(db_session, role="admin")
    alice = await _user(db_session)
    resp = await client.post(
        DEPTS_URL,
        json={"name": f"销售-{uuid.uuid4().hex[:5]}", "assign_scope": "self"},
        headers=await make_headers(admin),
    )
    assert resp.status_code == 201, resp.text
    dept_id = resp.json()["id"]

    put = await client.put(
        f"{DEPTS_URL}/users/{alice.id}/department",
        json={"department_id": dept_id},
        headers=await make_headers(admin),
    )
    assert put.status_code == 200 and put.json()["department_id"] == dept_id

    listed = await client.get(DEPTS_URL, headers=await make_headers(admin))
    assert listed.status_code == 200
    row = next(d for d in listed.json()["items"] if d["id"] == dept_id)
    assert row["member_count"] == 1
    assert row["assign_scope"] == "self"


async def test_dispatch_blocked_outside_scope(db_session, client, make_headers) -> None:
    """端到端：受限部门的人转交需求给范围外的人 → 403。"""
    await _user(db_session, role="admin")  # 建部门需要 admin 身份，但本用例走 service
    actor = await _user(db_session)
    outsider = await _user(db_session)

    narrow = await org_service.create_department(
        db_session,
        name=f"窄-{uuid.uuid4().hex[:5]}",
        parent_id=None,
        assign_scope="self",
    )
    wide = await org_service.create_department(
        db_session,
        name=f"宽-{uuid.uuid4().hex[:5]}",
        parent_id=None,
        assign_scope="all",
    )
    await org_service.set_user_department(
        db_session, user_id=actor.id, department_id=narrow.id
    )
    await org_service.set_user_department(
        db_session, user_id=outsider.id, department_id=wide.id
    )
    await db_session.commit()

    created = await client.post(
        REQUESTS_URL,
        json={"title": f"组织门禁-{uuid.uuid4().hex[:5]}"},
        headers=await make_headers(actor),
    )
    assert created.status_code == 201, created.text[:200]
    request_id = created.json()["id"]

    denied = await client.post(
        f"{REQUESTS_URL}/{request_id}/assign",
        json={"assignee_type": "user", "assignee_id": outsider.id},
        headers=await make_headers(actor),
    )
    assert denied.status_code == 403
    assert "部门" in denied.json()["error"]["message"]

    # 派给自己永远可以
    ok = await client.post(
        f"{REQUESTS_URL}/{request_id}/assign",
        json={"assignee_type": "user", "assignee_id": actor.id},
        headers=await make_headers(actor),
    )
    assert ok.status_code == 202, ok.text[:200]
