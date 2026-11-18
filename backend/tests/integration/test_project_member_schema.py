"""010 数据模型集成测试（T005）：迁移 0014 的 schema 断言 + 级联行为。

锁的是「不能靠单测糊过去」的部分：复合主键与索引在真实 PG 上成立、
删用户/删项目的级联方向（成员行消失，但**需求不消失**——共享关系是
派生数据，需求是已发生的事）。
"""

import uuid

import pytest
from sqlalchemy import text

from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.request import Request
from app.models.user import User

pytestmark = pytest.mark.integration


async def _user(db) -> User:
    user = User(
        email=f"pm-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="member",
        status="active",
    )
    db.add(user)
    await db.flush()
    return user


async def test_columns_and_composite_pk_exist(db_session):
    """列齐全；复合主键 (project_id, user_id) 存在（成员关系无第二行）。"""
    cols = (
        (
            await db_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'project_members'"
                )
            )
        )
        .scalars()
        .all()
    )
    assert {"project_id", "user_id", "created_at"} <= set(cols)

    pk_cols = (
        (
            await db_session.execute(
                text(
                    "SELECT a.attname FROM pg_index i "
                    "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                    "AND a.attnum = ANY(i.indkey) "
                    "WHERE i.indrelid = 'project_members'::regclass AND i.indisprimary"
                )
            )
        )
        .scalars()
        .all()
    )
    assert set(pk_cols) == {"project_id", "user_id"}


async def test_user_index_exists(db_session):
    """ix_project_members_user_id 存在（「我是哪些项目的成员」反查热路径）。"""
    names = set(
        (
            await db_session.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE indexname = 'ix_project_members_user_id'"
                )
            )
        )
        .scalars()
        .all()
    )
    assert "ix_project_members_user_id" in names


async def test_members_cascade_with_project_and_user(db_session):
    """删项目 → 成员行级联消失；删用户（DB 层）→ 其成员行级联消失。

    两个级联都是 CASCADE 声明在库层的直接断言。注意：删**持有需求的**
    用户在 ORM 层会先把 request.owner_id 置 NULL 撞 NOT NULL——系统本来
    就没有用户删除端点（users 路由只有注册/列表/options），这条路径纯属
    库级防御，所以用一个不持有需求的用户来验。
    """
    owner = await _user(db_session)
    member = await _user(db_session)
    author = await _user(db_session)
    project = Project(owner_id=owner.id, name=f"共享-{uuid.uuid4().hex[:4]}")
    db_session.add(project)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=member.id))
    # 需求由第三个用户持有（不是将被删除的 member）
    req = Request(owner_id=author.id, title="成员建的需求", project_id=project.id)
    db_session.add(req)
    await db_session.commit()

    # 删用户（无需求）→ 成员行没了
    await db_session.delete(member)
    await db_session.commit()
    left = int(
        (
            await db_session.execute(
                text("SELECT count(*) FROM project_members WHERE user_id = :u"),
                {"u": member.id},
            )
        ).scalar()
    )
    assert left == 0

    # 删项目 → 成员行级联消失；需求保留（008 的 SET NULL，project_id 清空）
    other = await _user(db_session)
    db_session.add(ProjectMember(project_id=project.id, user_id=other.id))
    await db_session.commit()
    await db_session.delete(project)
    await db_session.commit()
    left = int(
        (
            await db_session.execute(
                text("SELECT count(*) FROM project_members WHERE project_id = :p"),
                {"p": project.id},
            )
        ).scalar()
    )
    assert left == 0
    # 需求本体还在（直接查列绕身份映射）
    survived = int(
        (
            await db_session.execute(
                text("SELECT count(*) FROM requests WHERE id = :r"),
                {"r": req.id},
            )
        ).scalar()
    )
    assert survived == 1
