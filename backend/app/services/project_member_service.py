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
from app.api.messages import raise_for_missing
from app.models.project import Project
from app.models.project_member import ROLE_EDITOR, ROLE_VIEWER, ProjectMember
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
        raise_for_missing("project")
    if project.owner_id != user_id:
        # 先判成员：成员做 owner 动作是 403，陌生人是 404——两种「不够权」
        # 不混码（006/007 立下、010 延续的纪律）。
        if not await is_member(db, project_id, user_id):
            raise_for_missing("project")
        raise AppError("FORBIDDEN", "仅项目创建者可管理成员")
    return project


async def _load_project_for_transfer(
    db: AsyncSession, project_id: str, user_id: str
) -> Project:
    """移交前置：行锁 + 三级谓词（017，contracts §2 序 3–5）。

    用 `select(...).with_for_update()` 而不是 `db.get`：
    1. 身份映射里的对象可能是陈旧的（`_load_visible_project` 的注释同款理由）；
    2. 行锁是「移交 vs 改名/删除/踢人」并发交错的唯一防线（research R-09）。
    """
    project = (
        (
            await db.execute(
                select(Project).where(Project.id == project_id).with_for_update()
            )
        )
        .scalars()
        .first()
    )
    if project is None:
        raise_for_missing("project")
    if project.owner_id != user_id:
        if not await is_member(db, project_id, user_id):
            raise_for_missing("project")
        raise AppError("FORBIDDEN", "仅项目创建者可移交所有权")
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


async def member_role(db: AsyncSession, project_id: str, user_id: str) -> str | None:
    """我在该项目里的角色：`owner` / `editor` / `viewer` / None（非参与者）。

    owner 不是成员行（R-01），所以必须单独判 projects.owner_id——
    否则「创建者竟然不是 editor」会让写权限判定把 owner 自己挡住。
    """
    project = await db.get(Project, project_id)
    if project is None:
        return None
    if project.owner_id == user_id:
        return "owner"
    row = (
        (
            await db.execute(
                select(ProjectMember.role).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == user_id,
                )
            )
        )
        .scalars()
        .first()
    )
    return row


async def can_write_in_project(db: AsyncSession, project_id: str, user_id: str) -> bool:
    """该项目里能不能写（改需求 / 分配执行）。

    owner 与 editor 可以；viewer 与非参与者不行。读权限不走这里——
    只读成员依然要能看到项目与需求（那正是给他 role 的目的）。
    """
    return await member_role(db, project_id, user_id) in ("owner", ROLE_EDITOR)


async def add_member(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    member_user_id: str,
    role: str = ROLE_EDITOR,
) -> ProjectMember:
    """加成员：owner 权限 + 在职校验 + 重复 409 + 通知被加者。

    019：`role` 为 editor（默认）/ viewer（只读）。只读成员能看项目与项目内
    需求、不能改需求或分配——「给人看，别让他动」是最常见的协作诉求。
    """
    project = await _load_project_for_member_ops(db, project_id, user_id)

    # service 层再兜一道：schema 的 Literal 只覆盖 HTTP 入口，
    # 直接调 service（脚本/测试）时不该让非法角色落库。
    if role not in (ROLE_EDITOR, ROLE_VIEWER):
        raise AppError("INVALID_INPUT", "角色仅支持 editor / viewer")

    target = await db.get(User, member_user_id)
    if target is None:
        raise_for_missing("user")
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

    edge = ProjectMember(project_id=project_id, user_id=member_user_id, role=role)
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
        # R-04：无主项目是脏状态，所以 owner 不能把自己踢出去。
        # 017 之前这里只有「删项目」一条出路，现在多了「移交」——文案要
        # 指向正解，否则用户被赶向销毁数据的那条路（FR-009）。
        raise AppError(
            "CONFLICT",
            "创建者不能退出自己创建的项目：请先移交所有权，或删除项目",
        )

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
        raise_for_missing("member")

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


async def transfer_ownership(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    new_owner_id: str,
    keep_as_member: bool = True,
) -> Project:
    """移交项目所有权（017）：加锁 → 六步校验 → 归属/成员行 → 通知新 owner。

    为什么独立成函数而不是塞进 update_project：移交同时改归属、增删成员行、
    发通知、写独立审计，且 409 有四种语义（research R-01）。

    校验顺序（contracts §2，顺序本身是契约的一部分）：
    ① 归属（404/403）② 自移交 ③ 目标存在 ④ 目标在职 ⑤ 目标角色 ⑥ 成员关系
    ⑦ 同名冲突。①②③为什么必须在前见 research R-05：陌生人不能靠
    「传一个不存在的用户」探测项目是否存在。
    """
    project = await _load_project_for_transfer(db, project_id, user_id)

    if new_owner_id == user_id:
        raise AppError("CONFLICT", "不能移交给自己")

    target = await db.get(User, new_owner_id)
    if target is None:
        # 016 的显式例外：user 存在性经 /users/options 已对全体成员公开，
        # 且 010 的 add_member 就是这么回目标的（research R-03）。
        raise_for_missing("user")
    if target.status != "active":
        # 与 add_member 同款判断：账号态 ≠ 成员态，但停用账号不能接项目。
        raise AppError("INVALID_INPUT", "用户已停用，不能接收项目")
    if target.role == "viewer":
        # viewer 在 010 的模型里连项目入口都没有（nav memberOnly），
        # 让他当 owner 会造出「有项目但进不去」的怪状态。
        raise AppError("CONFLICT", "只读角色不能接收项目")

    if not await is_member(db, project_id, new_owner_id):
        # 移交是接力不是空降（research R-02）：先加成员再移交。
        raise AppError("CONFLICT", "目标用户不是项目成员，请先加为成员")

    # 同名检查放最后：它是唯一要读「目标用户名下项目」的检查，失败无副作用。
    # 函数内 import：project_service 顶层已 import 本模块，模块级互引会成环。
    from app.services import project_service

    await project_service.assert_name_available(
        db,
        new_owner_id,
        project.name,
        exclude_id=project.id,
        message=f"目标用户已有同名项目「{project.name}」，请先改名或换人",
    )

    old_owner_id = project.owner_id
    # 顺序：先删新 owner 的成员行，再改归属——避免任何中间态里
    # 「同一个人既是 owner 又在成员表」（010 R-01 的不变式）。
    edge = (
        (
            await db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == new_owner_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if edge is not None:
        await db.delete(edge)
        await db.flush()

    project.owner_id = new_owner_id

    if keep_as_member:
        # owner 不在成员表（010 R-01），所以原 owner 正常不会有行；
        # 仍查一次是防御：手工改过的库不该让 flush 抛唯一约束。
        already = (
            (
                await db.execute(
                    select(ProjectMember).where(
                        ProjectMember.project_id == project_id,
                        ProjectMember.user_id == old_owner_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if already is None:
            db.add(ProjectMember(project_id=project_id, user_id=old_owner_id))

    await db.flush()
    await create_notification(
        db,
        user_id=new_owner_id,
        type_="project_ownership_received",
        title=project.name,
        payload={
            "project_id": project_id,
            "project_name": project.name,
            "previous_owner_id": old_owner_id,
        },
    )
    logger.info(
        "project_transferred",
        project_id=project_id,
        from_user_id=old_owner_id,
        to_user_id=new_owner_id,
        keep_as_member=keep_as_member,
        by=user_id,
    )
    return project


async def list_members(
    db: AsyncSession, project_id: str, user_id: str
) -> list[tuple[str, str | None, bool, str, str]]:
    """成员列表（owner + 成员，owner 在前）。

    返回 `(user_id, email, is_owner, role, joined_at)`；owner 行的 role 恒为
    `owner`（不是成员行里的 editor/viewer——它在 owner_id 列上）。

    谓词：owner ∪ 成员（成员可见列表是 010 的可见性一部分）；
    陌生人 → 404。
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise_for_missing("project")
    if project.owner_id != user_id and not await is_member(db, project_id, user_id):
        raise_for_missing("project")

    owner = await db.get(User, project.owner_id)
    rows = [
        (
            project.owner_id,
            owner.email if owner else None,
            True,
            "owner",
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
        rows.append(
            (
                edge.user_id,
                user.email,
                False,
                edge.role,
                edge.created_at.isoformat(),
            )
        )
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
