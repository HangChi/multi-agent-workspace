"""项目成员服务（010）：成员 CRUD + 成员判定 + relevant 反查。

三级谓词在此收敛（contracts §1.2）：
- owner（projects.owner_id）：全权——加人/踢人/改名/归档/删除；
- 成员（project_members 一行）：可读项目与成员列表、项目内协作；
- 陌生人：404（不暴露存在性）。

错误码分工：成员做 owner 动作 = 403（他知道项目存在，不够权）；
陌生人碰项目一切 = 404。两种「不够权」不混码——这是 006/007 立下、
010 延续的纪律。
"""

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.services.notification_service import create_notification

logger = structlog.get_logger(__name__)


async def _load_project_for_member_ops(
    db: AsyncSession, project_id: str, user_id: str
) -> Project:
    """成员管理前置：三级谓词（contracts §1.2）。

    - 陌生人（非 owner 非成员）→ 404（不暴露存在性）；
    - 成员但非 owner → 403（他知道项目存在，不够权）；
    - owner → 返回项目。
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise AppError("NOT_FOUND", "项目不存在")
    if project.owner_id != user_id:
        # 先判成员：成员做 owner 动作是 403，陌生人是 404——两种「不够权」
        # 不混码（006/007 立下、010 延续的纪律）。
        if not await is_member(db, project_id, user_id):
            raise AppError("NOT_FOUND", "项目不存在")
        raise AppError("FORBIDDEN", "仅项目创建者可管理成员")
    return project


async def is_member(db: AsyncSession, project_id: str, user_id: str) -> bool:
    """成员判定（半连接由索引 ix_project_members_user_id 支撑）。"""
    row = (
        (
            await db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == user_id,
                )
            )
        )
        .scalars()
        .first()
    )
    return row is not None


async def member_project_ids(db: AsyncSession, user_id: str) -> list[str]:
    """我是哪些项目的成员（relevant 口径的一肢）。"""
    return list(
        (
            await db.execute(
                select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )


async def participating_project_ids(db: AsyncSession, user_id: str) -> list[str]:
    """我**参与**的项目（010）：我创建的 ∪ 我是成员的。

    与 member_project_ids 的差异是刻意存在的：项目 owner 不在成员表里
    （owner 恒为 projects.owner_id，R-01），所以任何「按成员行反查」的
    口径都会把 owner 本人漏掉——而 owner 恰恰是最该看到自己项目里全部
    需求的人。**所有需求/项目可见性口径用本函数，不要用成员行版本。**
    """
    owned = list(
        (await db.execute(select(Project.id).where(Project.owner_id == user_id)))
        .scalars()
        .all()
    )
    member_of = await member_project_ids(db, user_id)
    return list({*owned, *member_of})


async def is_participant(db: AsyncSession, project_id: str, user_id: str) -> bool:
    """我是否参与该项目（010）：owner ∪ 成员。

    与 is_member 的差异同上——成员行版本对 owner 返回 False，
    可见性快路必须用本函数。
    """
    project = await db.get(Project, project_id)
    if project is None:
        return False
    if project.owner_id == user_id:
        return True
    return await is_member(db, project_id, user_id)


async def add_member(
    db: AsyncSession, *, project_id: str, user_id: str, member_user_id: str
) -> ProjectMember:
    """加成员：owner 权限 + 在职校验 + 重复 409 + 通知被加者。"""
    project = await _load_project_for_member_ops(db, project_id, user_id)

    target = await db.get(User, member_user_id)
    if target is None:
        raise AppError("NOT_FOUND", "用户不存在")
    if target.status != "active":
        # 停用 400：账号态 ≠ 成员态，但停用账号不能进新项目（R-08）。
        # 与「不存在」的 404 区分——存在但不可用。
        raise AppError("INVALID_INPUT", "用户已停用，不能添加为项目成员")

    existing = (
        (
            await db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == member_user_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise AppError("CONFLICT", "该用户已是项目成员")

    edge = ProjectMember(project_id=project_id, user_id=member_user_id)
    db.add(edge)
    await db.flush()
    # 通知只发给对方本人（R-05：被加者要知道「为什么多了这些需求」）
    await create_notification(
        db,
        user_id=member_user_id,
        type_="project_member_added",
        title=project.name,
        payload={"project_id": project_id, "project_name": project.name},
    )
    logger.info(
        "project_member_added",
        project_id=project_id,
        member_user_id=member_user_id,
        by=user_id,
    )
    return edge


async def remove_member(
    db: AsyncSession, *, project_id: str, user_id: str, member_user_id: str
) -> None:
    """移除成员：owner 权限 + 自移除 409 + 通知被移出者。"""
    project = await _load_project_for_member_ops(db, project_id, user_id)

    if member_user_id == user_id:
        # R-04：无主项目是脏状态；所有权移交是后续特性
        raise AppError("CONFLICT", "创建者不能退出自己创建的项目（可删除项目）")

    edge = (
        (
            await db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == member_user_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if edge is None:
        raise AppError("NOT_FOUND", "成员关系不存在")

    await db.delete(edge)
    await db.flush()
    await create_notification(
        db,
        user_id=member_user_id,
        type_="project_member_removed",
        title=project.name,
        payload={"project_id": project_id, "project_name": project.name},
    )
    logger.info(
        "project_member_removed",
        project_id=project_id,
        member_user_id=member_user_id,
        by=user_id,
    )


async def list_members(
    db: AsyncSession, project_id: str, user_id: str
) -> list[tuple[str, str | None, bool, str]]:
    """成员列表（owner + 成员，owner 在前）。

    谓词：owner ∪ 成员（成员可见列表是 010 的可见性一部分）；
    陌生人 → 404。
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise AppError("NOT_FOUND", "项目不存在")
    if project.owner_id != user_id and not await is_member(db, project_id, user_id):
        raise AppError("NOT_FOUND", "项目不存在")

    owner = await db.get(User, project.owner_id)
    rows = [
        (
            project.owner_id,
            owner.email if owner else None,
            True,
            project.created_at.isoformat(),
        )
    ]
    members = (
        await db.execute(
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == project_id)
            .order_by(ProjectMember.created_at)
        )
    ).all()
    for edge, user in members:
        rows.append((edge.user_id, user.email, False, edge.created_at.isoformat()))
    return rows


async def member_count(db: AsyncSession, project_id: str) -> int:
    """成员数（含 owner——「这个项目有几个人」的自然口径，FR-009）。"""
    count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(ProjectMember)
                .where(ProjectMember.project_id == project_id)
            )
        ).scalar_one()
    )
    return count + 1  # + owner


async def participant_user_ids(db: AsyncSession, project_id: str) -> list[str]:
    """项目的全部参与者 user_id（010）：owner + 成员行。

    用途：assign 的目标池——共享项目里的 agent/组对参与者开放
    （成员拖执行时用 owner 建的 agent，否则协作链断在分配那一步）。
    """
    project = await db.get(Project, project_id)
    if project is None:
        return []
    member_ids = list(
        (
            await db.execute(
                select(ProjectMember.user_id).where(
                    ProjectMember.project_id == project_id
                )
            )
        )
        .scalars()
        .all()
    )
    return list({project.owner_id, *member_ids})
