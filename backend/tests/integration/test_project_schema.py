"""008 数据模型集成测试（T003）：迁移 0012 的 schema 断言 + SET NULL 行为。

锁的是「不能靠单测糊过去」的部分：projects 表的列/唯一约束/索引在真实
PG 上成立、删除项目把 requests.project_id 置空（而不是删需求）、
project_name 快照的清理责任在 service（外键不清它）。
"""

import uuid

import pytest
from sqlalchemy import select, text

from app.models.project import Project
from app.models.request import Request
from app.models.user import User

pytestmark = pytest.mark.integration


async def _user(db) -> User:
    user = User(
        email=f"proj-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="member",
        status="active",
    )
    db.add(user)
    await db.flush()
    return user


# ---------- 列与约束存在 ----------


async def test_projects_columns_and_unique_constraint(db_session):
    """projects 表列齐全；(owner_id, name) 唯一约束真实存在。

    唯一约束存在性用 pg_constraint 查——只在 ORM 层试插一条同名会因
    时序/缓存给出假阴性（SQLAlchemy 的 flush 顺序不等于约束生效顺序）。
    """
    cols = (
        (
            await db_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'projects'"
                )
            )
        )
        .scalars()
        .all()
    )
    assert {
        "id",
        "owner_id",
        "name",
        "description",
        "status",
        "created_at",
        "updated_at",
    } <= set(cols)

    constraint = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conrelid = 'projects'::regclass AND contype = 'u' "
                "AND conname = 'uq_projects_owner_name'"
            )
        )
    ).scalar()
    assert constraint == 1


async def test_projects_indexes_exist(db_session):
    """ix_projects_owner_status 存在（列表端点固定过滤路径）。"""
    names = set(
        (
            await db_session.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE indexname IN ('ix_projects_owner_status', "
                    "'ix_requests_project_id')"
                )
            )
        )
        .scalars()
        .all()
    )
    assert {"ix_projects_owner_status", "ix_requests_project_id"} <= names


async def test_request_project_columns_exist(db_session):
    """requests 新增两列，且默认可空（既有数据零迁移损失）。"""
    cols = (
        await db_session.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'requests' AND column_name IN "
                "('project_id', 'project_name')"
            )
        )
    ).all()
    by_name = {name: nullable for name, nullable in cols}
    assert set(by_name) == {"project_id", "project_name"}
    assert by_name["project_id"] == "YES"
    assert by_name["project_name"] == "YES"


# ---------- SET NULL 行为（R-02 的落点） ----------


async def test_delete_project_sets_request_project_null(db_session):
    """删除项目 → 需求保留、project_id 由外键置空；project_name 不清
    （service 的显式清空责任——这条用例同时钉住「外键只动 project_id」）。"""
    owner = await _user(db_session)
    project = Project(owner_id=owner.id, name=f"删我 {uuid.uuid4().hex[:4]}")
    db_session.add(project)
    await db_session.flush()
    req = Request(
        owner_id=owner.id,
        title="归属后删项目",
        project_id=project.id,
        project_name=project.name,
    )
    db_session.add(req)
    await db_session.commit()

    await db_session.delete(project)
    await db_session.commit()

    # 直接查列绕过身份映射：delete + commit 后，会话里那个 Request 对象的
    # project_id 还是删除前的陈旧值（DB 级 SET NULL 发生在 ORM 背后），
    # 用 get() 会拿到陈旧对象——007 踩过同款坑，这里钉住查法。
    refreshed = (
        await db_session.execute(
            select(Request.project_id, Request.project_name).where(Request.id == req.id)
        )
    ).one()
    assert refreshed is not None  # 需求没被级联删掉
    assert refreshed.project_id is None  # 外键 SET NULL 生效
    assert refreshed.project_name == project.name  # 快照列归 service 清（T009）


async def test_duplicate_project_name_rejected_by_constraint(db_session):
    """同用户名下同名项目：约束层直接拒（service 层先查给出友好 409）。"""
    owner = await _user(db_session)
    name = f"重名 {uuid.uuid4().hex[:4]}"
    db_session.add(Project(owner_id=owner.id, name=name))
    await db_session.flush()
    db_session.add(Project(owner_id=owner.id, name=name))
    with pytest.raises(Exception):  # noqa: B017 - IntegrityError 具体类型由 PG 决定
        await db_session.flush()
    await db_session.rollback()
